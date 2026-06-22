"""JSON API: report push (terrahawk → terrakettle) and admin management."""

import json
from pathlib import PurePosixPath
from typing import Optional

from fastapi import (APIRouter, Depends, Form, HTTPException, Request,
                     UploadFile, status)

from . import auth, db, notify, retention, schemas
from .config import get_settings
from .storage import get_storage

router = APIRouter(prefix="/api/v1")


# --- Admin: projects & tokens ----------------------------------------------

@router.post("/projects", response_model=schemas.ProjectOut,
             dependencies=[Depends(auth.require_admin)])
def create_project(body: schemas.ProjectIn):
    slug = body.slug.strip().lower()
    if not slug or "/" in slug:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid slug")
    if db.get_project(slug):
        raise HTTPException(status.HTTP_409_CONFLICT, "Project already exists")
    row = db.create_project(slug, body.name or slug)
    return schemas.ProjectOut(slug=row["slug"], name=row["name"])


@router.post("/projects/{slug}/tokens", response_model=schemas.TokenOut,
             dependencies=[Depends(auth.require_admin)])
def mint_token(slug: str, label: Optional[str] = Form(default=None),
               ttl_days: Optional[int] = Form(default=None)):
    project = db.get_project(slug)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown project")
    token = auth.generate_token(slug)
    ttl = ttl_days if ttl_days is not None else get_settings().token_ttl_days
    db.add_token(project["id"], auth.hash_token(token), label,
                 auth.token_expiry(ttl))
    return schemas.TokenOut(token=token, project=slug)


@router.delete("/projects/{slug}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(auth.require_admin)])
def delete_project(slug: str):
    """Delete a project, its tokens/runs, and all stored report files."""
    runs = db.delete_project(slug)
    storage = get_storage()
    for run in runs:
        for key in (run["html_key"], run["data_js_key"], run["json_key"]):
            if key:
                try:
                    storage.delete(key)
                except Exception:
                    pass


@router.get("/projects/{slug}/tokens",
            dependencies=[Depends(auth.require_admin)])
def list_tokens(slug: str):
    project = db.get_project(slug)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown project")
    return [dict(t) for t in db.list_tokens(project["id"])]


@router.delete("/projects/{slug}/tokens/{token_id}",
               status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(auth.require_admin)])
def revoke_token(slug: str, token_id: int):
    project = db.get_project(slug)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown project")
    if db.revoke_token(project["id"], token_id) == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown token")


# --- Push: terrahawk uploads a report --------------------------------------

def _read_capped(upload: UploadFile, cap: int) -> bytes:
    data = upload.file.read(cap + 1)
    if len(data) > cap:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"{upload.filename} exceeds {cap} bytes",
        )
    return data


@router.post("/runs", status_code=status.HTTP_201_CREATED)
async def push_run(
    request: Request,
    report: UploadFile,
    html: Optional[UploadFile] = None,
    data_js: Optional[UploadFile] = None,
    run_id: Optional[str] = Form(default=None),
    project=Depends(auth.require_project),
):
    """Receive a Terrahawk report (JSON required; HTML + data.js optional).

    The pushing token determines which project the run is filed under.
    """
    from .config import get_settings
    cap = get_settings().max_upload_bytes

    json_bytes = _read_capped(report, cap)
    try:
        results = json.loads(json_bytes)
        if not isinstance(results, list):
            raise ValueError("expected a JSON array")
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Invalid report JSON: {e}")

    # Derive run_id from the uploaded report filename when not supplied.
    rid = (run_id or PurePosixPath(report.filename or "").stem or "").strip()
    if not rid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "run_id required (or name the report file)")
    rid = PurePosixPath(rid).name  # defend against path traversal

    storage = get_storage()
    slug = project["slug"]
    prev = db.get_run(project["id"], rid)  # for orphan cleanup on re-push
    keys: dict = {}
    names: dict = {}

    json_name = f"{rid}.json"
    keys["json"] = storage.full_key(slug, rid, json_name)
    storage.put(keys["json"], json_bytes, "application/json")

    if html is not None:
        hname = PurePosixPath(html.filename or f"{rid}.html").name
        names["html"] = hname
        keys["html"] = storage.full_key(slug, rid, hname)
        storage.put(keys["html"], _read_capped(html, cap), "text/html")

    if data_js is not None:
        dname = PurePosixPath(data_js.filename or f"{rid}_data.js").name
        names["data_js"] = dname
        keys["data_js"] = storage.full_key(slug, rid, dname)
        storage.put(keys["data_js"], _read_capped(data_js, cap),
                    "application/javascript")

    summary = schemas.summarize(results)
    db.upsert_run(
        project["id"], rid, summary, keys, names,
        schemas.report_date_from_run_id(rid),
    )

    # Drop any payloads the previous version of this run left orphaned (e.g.
    # a re-push whose file names changed).
    if prev is not None:
        new_keys = set(keys.values())
        for old in (prev["html_key"], prev["data_js_key"], prev["json_key"]):
            if old and old not in new_keys:
                try:
                    storage.delete(old)
                except Exception:
                    pass

    # Enforce per-project retention, if configured.
    keep = get_settings().max_runs_per_project
    if keep > 0:
        retention.prune_project(project["id"], keep)

    base = (get_settings().public_url or str(request.base_url)).rstrip("/")
    report_url = f"{base}/p/{slug}/runs/{rid}/" if "html" in keys else None

    # Fire-and-forget notification on drift/errors (no-op unless configured).
    notify.maybe_notify(slug, rid, summary, report_url)

    return {
        "run_id": rid,
        "project": slug,
        **summary,
        "report_url": report_url,
        "json_url": f"{base}/p/{slug}/runs/{rid}/{json_name}",
    }
