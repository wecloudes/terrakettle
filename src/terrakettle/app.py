"""FastAPI application factory."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from . import (api, auth, badge, compare, db, feed, metrics, web)
from .__init__ import __version__
from .config import get_settings

# Web paths reachable without a view session.
_PUBLIC_PATHS = {"/login", "/logout", "/healthz", "/metrics"}


@asynccontextmanager
async def _lifespan(app: FastAPI):
    errors = get_settings().validate_startup()
    if errors:
        raise RuntimeError("; ".join(errors))
    db.init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Terrakettle", version=__version__, lifespan=_lifespan)

    @app.middleware("http")
    async def _guard_views(request: Request, call_next):
        """Gate browser views behind a session when a view password is set.

        API routes carry their own bearer auth and are exempt; report payloads
        and assets are exempt so they load once the parent page is authorized.
        """
        path = request.url.path
        if (auth.view_auth_enabled()
                and not path.startswith("/api/")
                and path not in _PUBLIC_PATHS
                and not path.endswith("/badge.svg")  # public shields
                and request.method == "GET"
                and not auth.verify_session(request.cookies.get("tk_session"))):
            return RedirectResponse(
                f"/login?next={path}", status_code=302
            )
        return await call_next(request)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return metrics.check_health(__version__)

    app.include_router(api.router)
    app.include_router(web.router)
    app.include_router(badge.router)
    app.include_router(compare.router)
    app.include_router(feed.router)
    app.include_router(metrics.router)
    return app


app = create_app()
