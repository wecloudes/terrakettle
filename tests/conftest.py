"""Shared pytest fixtures for the Terrakettle FastAPI app.

CRITICAL: a ``terrakettle.py`` file at the repo root shadows the real
``terrakettle`` package when pytest puts the repo root on ``sys.path``.
The very first thing we do is prepend ``src/`` so ``import terrakettle``
resolves to the package, not the stray module.
"""

import sys
import pathlib

# --- Resolve the package, not the repo-root shadow module -------------------
_SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC))

import os  # noqa: E402
import importlib  # noqa: E402

import pytest  # noqa: E402


ADMIN_KEY = "test-admin"


def _set_base_env(monkeypatch, tmp_path):
    """Point the app at isolated tmp paths and a known admin key."""
    db_path = tmp_path / "t.db"
    data_dir = tmp_path / "data"
    monkeypatch.setenv("TERRAKETTLE_ADMIN_KEY", ADMIN_KEY)
    monkeypatch.setenv("TERRAKETTLE_DB_PATH", str(db_path))
    monkeypatch.setenv("TERRAKETTLE_STORAGE_BACKEND", "local")
    monkeypatch.setenv("TERRAKETTLE_STORAGE_BUCKET", str(data_dir))
    monkeypatch.setenv("TERRAKETTLE_INSECURE", "true")
    # Make sure no view password / retention leaks in from a real .env.
    monkeypatch.delenv("TERRAKETTLE_VIEW_PASSWORD", raising=False)
    monkeypatch.delenv("TERRAKETTLE_MAX_RUNS_PER_PROJECT", raising=False)


def _fresh_app():
    """Build a brand-new FastAPI app with the *current* environment.

    Settings are ``lru_cache``d and the storage backend is a module-global
    singleton, so both must be reset before (re)building the app.
    """
    from terrakettle.config import get_settings
    get_settings.cache_clear()

    from terrakettle import storage
    storage._instance = None

    from terrakettle import app as app_module
    importlib.reload  # no-op reference; create_app reads live settings
    return app_module.create_app()


@pytest.fixture
def make_client(monkeypatch, tmp_path):
    """Factory: build an entered TestClient with optional extra env.

    Usage::

        client = make_client()                       # base config
        client = make_client(VIEW_PASSWORD="secret")  # extra env (no prefix)
    """
    from fastapi.testclient import TestClient

    _set_base_env(monkeypatch, tmp_path)
    entered = []

    def _build(**extra_env):
        for key, value in extra_env.items():
            monkeypatch.setenv(f"TERRAKETTLE_{key}", str(value))
        app = _fresh_app()
        client = TestClient(app)
        client.__enter__()  # triggers lifespan startup (db.init_db)
        entered.append(client)
        return client

    yield _build

    for client in entered:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    """A ready-to-use entered TestClient with the base config."""
    return make_client()


# --- Helpers ----------------------------------------------------------------

def admin_headers():
    return {"Authorization": f"Bearer {ADMIN_KEY}"}


def push_files(report, *, html=None, data_js=None):
    """Build the ``files=`` mapping for a multipart push.

    ``report`` is a list of ``{"unit","status"}`` dicts (serialized to JSON).
    """
    import json as _json

    body = _json.dumps(report).encode()
    files = {"report": ("report.json", body, "application/json")}
    if html is not None:
        files["html"] = ("report.html", html.encode(), "text/html")
    if data_js is not None:
        files["data_js"] = ("report_data.js", data_js.encode(),
                            "application/javascript")
    return files


def make_project(client, slug="demo", name="Demo"):
    """Create a project and return the response."""
    return client.post("/api/v1/projects", json={"slug": slug, "name": name},
                       headers=admin_headers())


def mint_token(client, slug="demo", **form):
    """Mint a push token for ``slug`` and return the raw token string."""
    resp = client.post(f"/api/v1/projects/{slug}/tokens", data=form or {},
                       headers=admin_headers())
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def token_headers(token):
    return {"Authorization": f"Bearer {token}"}
