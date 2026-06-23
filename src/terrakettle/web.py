"""HTML views: login, project list, run history, and report serving."""

import json
import math
import mimetypes
from pathlib import PurePosixPath

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import (HTMLResponse, RedirectResponse, Response)

from . import auth, db
from .config import get_settings
from .storage import get_storage
from .templating import templates as _templates

router = APIRouter()

# Order used when previewing a run's units: worst first.
_STATUS_ORDER = {"error": 0, "timeout": 1, "drift": 2, "clean": 3}


# --- Trend sparkline --------------------------------------------------------

def _sparkline(rows, width=160, height=32) -> str:
    """Inline SVG sparkline of drift+error counts over recent runs."""
    if not rows:
        return ""
    vals = [(r["drift"] or 0) + (r["error"] or 0) for r in rows]
    hi = max(vals) or 1
    n = len(vals)
    step = width / max(n - 1, 1)
    pts = []
    for i, v in enumerate(vals):
        x = i * step
        y = height - 2 - (v / hi) * (height - 4)
        pts.append(f"{x:.1f},{y:.1f}")
    stroke = "#f85149" if vals[-1] else "#3fb950"
    poly = " ".join(pts)
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="none" role="img" aria-label="drift trend">'
        f'<polyline points="{poly}" fill="none" stroke="{stroke}" '
        f'stroke-width="1.5"/></svg>'
    )


# --- Auth (login / logout) --------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/"):
    if not auth.view_auth_enabled():
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)
    return _templates.TemplateResponse(
        request, "login.html", {"next": next, "error": None}
    )


@router.post("/login")
def login_submit(request: Request, password: str = Form(...),
                 next: str = Form(default="/")):
    if not auth.check_view_password(password):
        return _templates.TemplateResponse(
            request, "login.html",
            {"next": next, "error": "Incorrect password"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    target = next if next.startswith("/") else "/"  # open-redirect guard
    resp = RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(
        "tk_session", auth.make_session(),
        max_age=get_settings().session_ttl, httponly=True, samesite="lax",
    )
    return resp


@router.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    resp.delete_cookie("tk_session")
    return resp


# --- Listings ---------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    return _templates.TemplateResponse(
        request, "index.html",
        {"projects": db.list_projects(),
         "auth_enabled": auth.view_auth_enabled()},
    )


@router.get("/p/{slug}", response_class=HTMLResponse)
def project_page(request: Request, slug: str, page: int = 1,
                 status_filter: str = ""):
    project = db.get_project(slug)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown project")
    size = get_settings().page_size
    page = max(page, 1)
    total = db.count_runs(project["id"], status_filter or None)
    pages = max(math.ceil(total / size), 1)
    runs = db.list_runs(project["id"], limit=size, offset=(page - 1) * size,
                        status=status_filter or None)
    trend = db.runs_for_trend(project["id"])
    latest_rows = db.list_runs(project["id"], limit=1)
    return _templates.TemplateResponse(
        request, "project.html",
        {"project": project, "runs": runs, "page": page, "pages": pages,
         "total": total, "status_filter": status_filter,
         "sparkline": _sparkline(trend),
         "latest": latest_rows[0] if latest_rows else None},
    )


# --- Run detail + report serving --------------------------------------------

@router.get("/p/{slug}/runs/{run_id}", response_class=HTMLResponse)
def run_detail(request: Request, slug: str, run_id: str):
    """Terrakettle chrome around a run: stat cards, coverage, unit preview."""
    project, run = _resolve_run(slug, run_id)
    units = _load_units(run)
    return _templates.TemplateResponse(
        request, "run.html",
        {"project": project, "run": run, "units": units},
    )


def _load_units(run):
    """Load a run's unit list from its stored JSON for the detail preview.

    Returns a list of {unit, status, summary} sorted worst-first, or None if
    the payload can't be read/parsed.
    """
    if not run["json_key"]:
        return None
    try:
        data = json.loads(get_storage().get(run["json_key"]))
        if not isinstance(data, list):
            return None
    except Exception:
        return None
    units = [
        {"unit": u.get("unit", ""), "status": u.get("status", ""),
         "summary": u.get("summary", "")}
        for u in data if isinstance(u, dict)
    ]
    units.sort(key=lambda u: (_STATUS_ORDER.get(u["status"], 9), u["unit"]))
    return units


@router.get("/p/{slug}/runs/{run_id}/", response_class=HTMLResponse)
def run_report(request: Request, slug: str, run_id: str):
    """Serve the stored Terrahawk HTML report (its data.js loads as a sibling)."""
    project, run = _resolve_run(slug, run_id)
    if not run["html_key"]:
        return _templates.TemplateResponse(
            request, "no_report.html",
            {"project": project, "run": run}, status_code=200,
        )
    data = get_storage().get(run["html_key"])
    return HTMLResponse(content=data.decode("utf-8", "replace"),
                        headers=_report_headers())


@router.get("/p/{slug}/runs/{run_id}/{filename}")
def run_file(slug: str, run_id: str, filename: str):
    """Serve a sibling file of a report (data.js, json) from storage."""
    _, run = _resolve_run(slug, run_id)
    name = PurePosixPath(filename).name  # no traversal
    storage = get_storage()
    key = storage.full_key(slug, run_id, name)
    # Only serve files actually recorded for this run.
    allowed = {run["html_name"], run["data_js_name"], f"{run_id}.json"}
    if name not in {a for a in allowed if a}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such file")

    # Prefer a short-lived direct URL so the object store serves the bytes.
    settings = get_settings()
    if settings.signed_urls:
        url = storage.presign(key, settings.signed_url_ttl)
        if url:
            return RedirectResponse(url, status_code=status.HTTP_302_FOUND)

    try:
        data = storage.get(key)
    except Exception:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such file")
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if name.endswith(".js"):
        ctype = "application/javascript"
    return Response(content=data, media_type=ctype, headers=_report_headers())


def _report_headers() -> dict:
    return {
        "Content-Security-Policy": get_settings().report_csp,
        "X-Content-Type-Options": "nosniff",
    }


def _resolve_run(slug: str, run_id: str):
    project = db.get_project(slug)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown project")
    run = db.get_run(project["id"], run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown run")
    return project, run
