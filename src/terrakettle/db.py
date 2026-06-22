"""SQLite metadata index: projects, push tokens, and run summaries.

The report payloads themselves (HTML / data.js / JSON) live in object
storage; this database only holds the metadata needed to list and locate
them quickly.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from .config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,
    label       TEXT,
    created_at  TEXT NOT NULL,
    last_used_at TEXT,
    expires_at  TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    run_id        TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    report_date   TEXT,
    total         INTEGER NOT NULL DEFAULT 0,
    clean         INTEGER NOT NULL DEFAULT 0,
    drift         INTEGER NOT NULL DEFAULT 0,
    error         INTEGER NOT NULL DEFAULT 0,
    timeout       INTEGER NOT NULL DEFAULT 0,
    html_key      TEXT,
    data_js_key   TEXT,
    json_key      TEXT,
    html_name     TEXT,
    data_js_name  TEXT,
    UNIQUE(project_id, run_id)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(get_settings().db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# Ordered, additive migrations. Each runs once; PRAGMA user_version tracks the
# applied level. Index 0 => migration to version 1, etc.
_MIGRATIONS = [
    # v1: token expiry column (no-op if the base schema already added it).
    ["ALTER TABLE tokens ADD COLUMN expires_at TEXT"],
]


def _column_exists(conn, table, column) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def init_db() -> None:
    """Create the schema (if absent) and apply pending migrations under WAL."""
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_SCHEMA)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for i in range(version, len(_MIGRATIONS)):
            for stmt in _MIGRATIONS[i]:
                # Tolerate columns the fresh schema already provides.
                if stmt.startswith("ALTER TABLE tokens ADD COLUMN expires_at"):
                    if _column_exists(conn, "tokens", "expires_at"):
                        continue
                conn.execute(stmt)
        conn.execute(f"PRAGMA user_version = {len(_MIGRATIONS)}")


# --- Projects ---------------------------------------------------------------

def create_project(slug: str, name: str) -> sqlite3.Row:
    with connect() as conn:
        conn.execute(
            "INSERT INTO projects (slug, name, created_at) VALUES (?, ?, ?)",
            (slug, name, _now()),
        )
        return conn.execute(
            "SELECT * FROM projects WHERE slug = ?", (slug,)
        ).fetchone()


def get_project(slug: str) -> Optional[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM projects WHERE slug = ?", (slug,)
        ).fetchone()


def list_projects() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            """
            SELECT p.*,
                   (SELECT COUNT(*) FROM runs r WHERE r.project_id = p.id) AS run_count,
                   (SELECT MAX(created_at) FROM runs r WHERE r.project_id = p.id) AS last_run_at,
                   (SELECT r.drift FROM runs r WHERE r.project_id = p.id
                    ORDER BY r.run_id DESC LIMIT 1) AS last_drift,
                   (SELECT r.error FROM runs r WHERE r.project_id = p.id
                    ORDER BY r.run_id DESC LIMIT 1) AS last_error
            FROM projects p
            ORDER BY p.name
            """
        ).fetchall()


def delete_project(slug: str) -> list[sqlite3.Row]:
    """Delete a project; return its run rows so callers can purge storage."""
    with connect() as conn:
        project = conn.execute(
            "SELECT id FROM projects WHERE slug = ?", (slug,)
        ).fetchone()
        if project is None:
            return []
        runs = conn.execute(
            "SELECT * FROM runs WHERE project_id = ?", (project["id"],)
        ).fetchall()
        conn.execute("DELETE FROM projects WHERE id = ?", (project["id"],))
        return runs


# --- Tokens -----------------------------------------------------------------

def add_token(project_id: int, token_hash: str, label: Optional[str],
              expires_at: Optional[str] = None) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO tokens (project_id, token_hash, label, created_at, "
            "expires_at) VALUES (?, ?, ?, ?, ?)",
            (project_id, token_hash, label, _now(), expires_at),
        )


def list_tokens(project_id: int) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT id, label, created_at, last_used_at, expires_at FROM tokens "
            "WHERE project_id = ? ORDER BY id",
            (project_id,),
        ).fetchall()


def revoke_token(project_id: int, token_id: int) -> int:
    """Delete a token by id (scoped to its project). Returns rows removed."""
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM tokens WHERE id = ? AND project_id = ?",
            (token_id, project_id),
        )
        return cur.rowcount


def project_for_token(token_hash: str) -> Optional[sqlite3.Row]:
    """Return the project a non-expired token grants push access to, or None."""
    with connect() as conn:
        row = conn.execute(
            """
            SELECT p.*, t.expires_at AS _token_expires FROM tokens t
            JOIN projects p ON p.id = t.project_id
            WHERE t.token_hash = ?
            """,
            (token_hash,),
        ).fetchone()
        if row is None:
            return None
        exp = row["_token_expires"]
        if exp and exp < _now():
            return None  # expired
        conn.execute(
            "UPDATE tokens SET last_used_at = ? WHERE token_hash = ?",
            (_now(), token_hash),
        )
        return row


# --- Runs -------------------------------------------------------------------

def upsert_run(
    project_id: int,
    run_id: str,
    summary: dict,
    keys: dict,
    names: dict,
    report_date: Optional[str],
) -> sqlite3.Row:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO runs (project_id, run_id, created_at, report_date,
                              total, clean, drift, error, timeout,
                              html_key, data_js_key, json_key,
                              html_name, data_js_name)
            VALUES (:pid, :rid, :now, :rdate,
                    :total, :clean, :drift, :error, :timeout,
                    :html_key, :data_js_key, :json_key,
                    :html_name, :data_js_name)
            ON CONFLICT(project_id, run_id) DO UPDATE SET
                created_at = :now, report_date = :rdate,
                total = :total, clean = :clean, drift = :drift,
                error = :error, timeout = :timeout,
                html_key = :html_key, data_js_key = :data_js_key,
                json_key = :json_key, html_name = :html_name,
                data_js_name = :data_js_name
            """,
            {
                "pid": project_id, "rid": run_id, "now": _now(),
                "rdate": report_date,
                "total": summary.get("total", 0),
                "clean": summary.get("clean", 0),
                "drift": summary.get("drift", 0),
                "error": summary.get("error", 0),
                "timeout": summary.get("timeout", 0),
                "html_key": keys.get("html"),
                "data_js_key": keys.get("data_js"),
                "json_key": keys.get("json"),
                "html_name": names.get("html"),
                "data_js_name": names.get("data_js"),
            },
        )
        return conn.execute(
            "SELECT * FROM runs WHERE project_id = ? AND run_id = ?",
            (project_id, run_id),
        ).fetchone()


def list_runs(project_id: int, limit: Optional[int] = None,
              offset: int = 0, status: Optional[str] = None) -> list[sqlite3.Row]:
    """List runs newest-first, optionally filtered to those with a given
    non-zero status count, with limit/offset pagination."""
    where = "WHERE project_id = ?"
    params: list = [project_id]
    if status in ("clean", "drift", "error", "timeout"):
        where += f" AND {status} > 0"
    sql = f"SELECT * FROM runs {where} ORDER BY run_id DESC"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params += [limit, offset]
    with connect() as conn:
        return conn.execute(sql, params).fetchall()


def count_runs(project_id: int, status: Optional[str] = None) -> int:
    where = "WHERE project_id = ?"
    params: list = [project_id]
    if status in ("clean", "drift", "error", "timeout"):
        where += f" AND {status} > 0"
    with connect() as conn:
        return conn.execute(
            f"SELECT COUNT(*) FROM runs {where}", params
        ).fetchone()[0]


def runs_for_trend(project_id: int, limit: int = 60) -> list[sqlite3.Row]:
    """Recent runs in chronological order for drift/error trend charts."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT run_id, report_date, total, clean, drift, error, timeout "
            "FROM runs WHERE project_id = ? ORDER BY run_id DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
    return list(reversed(rows))


def get_run(project_id: int, run_id: str) -> Optional[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM runs WHERE project_id = ? AND run_id = ?",
            (project_id, run_id),
        ).fetchone()


def delete_run_row(run_pk: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM runs WHERE id = ?", (run_pk,))


def runs_beyond(project_id: int, keep: int) -> list[sqlite3.Row]:
    """Runs to prune when keeping only the newest ``keep`` for a project."""
    if keep <= 0:
        return []
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM runs WHERE project_id = ? "
            "ORDER BY run_id DESC LIMIT -1 OFFSET ?",
            (project_id, keep),
        ).fetchall()


def runs_older_than(cutoff_iso: str) -> list[sqlite3.Row]:
    """All runs created before the given ISO timestamp, across projects."""
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM runs WHERE created_at < ? ORDER BY created_at",
            (cutoff_iso,),
        ).fetchall()


def all_project_ids() -> list[int]:
    with connect() as conn:
        return [r["id"] for r in conn.execute("SELECT id FROM projects")]
