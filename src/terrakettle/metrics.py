"""Prometheus metrics and health probing for terrakettle."""

from __future__ import annotations

from fastapi import APIRouter, Response

from . import db
from .__init__ import __version__
from .storage import get_storage

router = APIRouter()

_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _escape_label(value: str) -> str:
    """Escape a Prometheus label value (backslash, double-quote, newline)."""
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )


def _number(value) -> str:
    """Render a metric value, falling back to 0 for None/invalid."""
    if value is None:
        return "0"
    try:
        if isinstance(value, bool):
            return "1" if value else "0"
        num = float(value)
    except (TypeError, ValueError):
        return "0"
    if num.is_integer():
        return str(int(num))
    return repr(num)


def _render_metrics() -> str:
    version = __version__ or "unknown"

    lines: list[str] = []

    lines.append("# HELP terrakettle_build_info Build information.")
    lines.append("# TYPE terrakettle_build_info gauge")
    lines.append(
        f'terrakettle_build_info{{version="{_escape_label(str(version))}"}} 1'
    )

    try:
        projects = list(db.list_projects())
    except Exception:
        # DB unavailable: still expose build_info above.
        return "\n".join(lines) + "\n"

    projects_total = len(projects)
    runs_total = 0
    for project in projects:
        try:
            runs_total += int(project["run_count"] or 0)
        except (TypeError, ValueError, IndexError):
            continue

    lines.append("# HELP terrakettle_projects_total Number of projects.")
    lines.append("# TYPE terrakettle_projects_total gauge")
    lines.append(f"terrakettle_projects_total {projects_total}")

    lines.append("# HELP terrakettle_runs_total Total number of runs across projects.")
    lines.append("# TYPE terrakettle_runs_total gauge")
    lines.append(f"terrakettle_runs_total {runs_total}")

    lines.append(
        "# HELP terrakettle_project_last_drift Last drift value per project."
    )
    lines.append("# TYPE terrakettle_project_last_drift gauge")
    for project in projects:
        label = _escape_label(str(project["slug"]))
        value = _number(project["last_drift"])
        lines.append(
            f'terrakettle_project_last_drift{{project="{label}"}} {value}'
        )

    lines.append(
        "# HELP terrakettle_project_last_error Last error value per project."
    )
    lines.append("# TYPE terrakettle_project_last_error gauge")
    for project in projects:
        label = _escape_label(str(project["slug"]))
        value = _number(project["last_error"])
        lines.append(
            f'terrakettle_project_last_error{{project="{label}"}} {value}'
        )

    return "\n".join(lines) + "\n"


@router.get("/metrics")
def metrics() -> Response:
    """Expose metrics in the Prometheus text exposition format."""
    return Response(content=_render_metrics(), media_type=_CONTENT_TYPE)


def check_health(version: str) -> dict:
    """Probe DB and storage for the /healthz handler. Never raises."""
    db_ok = False
    try:
        with db.connect() as conn:
            conn.execute("SELECT 1")
        db_ok = True
    except Exception:
        db_ok = False

    storage_ok = False
    try:
        get_storage()
        storage_ok = True
    except Exception:
        storage_ok = False

    return {
        "status": "ok" if db_ok and storage_ok else "degraded",
        "version": version,
        "db": db_ok,
        "storage": storage_ok,
    }
