"""Per-project syndication feeds (JSON and RSS 2.0).

Exposes the latest Terrahawk runs for a project as a machine-readable
feed, suitable for polling or aggregation. Links are absolute when a
``public_url`` is configured, otherwise relative.
"""

import json
from datetime import datetime
from email.utils import format_datetime
from xml.sax.saxutils import escape

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response

from . import db
from .config import get_settings

router = APIRouter()


def _base_url() -> str:
    """Return the configured public base URL without a trailing slash."""
    return get_settings().public_url.rstrip("/")


def _run_url(slug: str, run_id: str) -> str:
    """Build an absolute (or relative, when no public URL) run link."""
    path = f"/p/{slug}/runs/{run_id}"
    base = _base_url()
    return f"{base}{path}" if base else path


def _summary(run) -> str:
    """Render a one-line summary string for a run row."""
    return (
        f"{run['drift']} drift, {run['error']} error, "
        f"{run['clean']} clean / {run['total']} units"
    )


def _require_project(slug: str):
    project = db.get_project(slug)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


@router.get("/p/{slug}/feed.json")
def feed_json(slug: str) -> JSONResponse:
    project = _require_project(slug)
    runs = db.list_runs(project["id"], limit=50)

    items = []
    for run in runs:
        items.append(
            {
                "run_id": run["run_id"],
                "url": _run_url(slug, run["run_id"]),
                "date": run["report_date"] or run["created_at"],
                "total": run["total"],
                "clean": run["clean"],
                "drift": run["drift"],
                "error": run["error"],
                "timeout": run["timeout"],
                "summary": _summary(run),
            }
        )

    payload = {
        "project": slug,
        "title": f"{project['name']} — Terrahawk runs",
        "items": items,
    }
    return JSONResponse(content=payload)


@router.get("/p/{slug}/feed.xml")
def feed_xml(slug: str) -> Response:
    project = _require_project(slug)
    runs = db.list_runs(project["id"], limit=50)

    channel_title = f"{project['name']} — Terrahawk runs"
    channel_link = _base_url() or "/"

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "<channel>",
        f"<title>{escape(channel_title)}</title>",
        f"<link>{escape(channel_link)}</link>",
        f"<description>{escape(channel_title)}</description>",
    ]

    for run in runs:
        run_id = run["run_id"]
        summary = _summary(run)
        item_title = f"{run_id} — {summary}"
        link = _run_url(slug, run_id)
        guid = f"{slug}/{run_id}"

        parts.append("<item>")
        parts.append(f"<title>{escape(item_title)}</title>")
        parts.append(f"<link>{escape(link)}</link>")
        parts.append(f'<guid isPermaLink="false">{escape(guid)}</guid>')

        try:
            dt = datetime.fromisoformat(run["created_at"])
            parts.append(f"<pubDate>{escape(format_datetime(dt))}</pubDate>")
        except (ValueError, TypeError):
            pass

        parts.append(f"<description>{escape(summary)}</description>")
        parts.append("</item>")

    parts.append("</channel>")
    parts.append("</rss>")

    xml = "\n".join(parts)
    return Response(content=xml, media_type="application/rss+xml")
