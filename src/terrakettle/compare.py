"""HTML view: compare the unit results of two runs of a project."""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import db
from .storage import get_storage

router = APIRouter()
_templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Severity ranking: higher == worse. Used to sort changes (regressions first)
# and to decide whether a status change is a regression or an improvement.
_SEVERITY = {"clean": 0, "drift": 1, "timeout": 2, "error": 3}


def _load_units(json_key):
    """Load a run's JSON array and return {unit_path: status}.

    Returns ``None`` if the object can't be fetched or parsed, so callers can
    show a friendly message instead of a 500.
    """
    if not json_key:
        return None
    try:
        raw = get_storage().get(json_key)
        data = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    units = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        unit = item.get("unit")
        if unit is None:
            continue
        units[str(unit)] = item.get("status")
    return units


def _classify(base_status, target_status):
    """Categorise a single unit given its base/target statuses."""
    if base_status is None:
        return "added"
    if target_status is None:
        return "removed"
    if base_status != target_status:
        return "changed"
    return "same"


def _sort_key(row):
    """Order: regressions first (biggest worsening), then improvements,
    then added, then removed. Within each, by severity descending."""
    b = _SEVERITY.get(row["base_status"], -1)
    t = _SEVERITY.get(row["target_status"], -1)
    if row["change"] == "changed":
        # delta > 0 == regression (target worse than base)
        delta = t - b
        return (0, -delta, -t, row["unit"])
    if row["change"] == "added":
        return (1, -t, row["unit"])
    # removed
    return (2, -b, row["unit"])


@router.get("/p/{slug}/compare", response_class=HTMLResponse)
def compare(request: Request, slug: str, target: str = "", base: str = ""):
    project = db.get_project(slug)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown project")

    all_runs = db.list_runs(project["id"])  # newest-first
    run_ids = [r["run_id"] for r in all_runs]

    ctx = {
        "request": request,
        "project": project,
        "run_ids": run_ids,
        "target": target or None,
        "base": base or None,
        "target_run": None,
        "base_run": None,
        "rows": None,
        "summary": None,
        "error": None,
        "no_earlier": False,
    }

    # No target selected yet: just show the selector form.
    if not target:
        return _templates.TemplateResponse(request, "compare.html", ctx)

    target_run = db.get_run(project["id"], target)
    if target_run is None:
        ctx["error"] = f"Unknown run “{target}”."
        return _templates.TemplateResponse(
            request, "compare.html", ctx,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    ctx["target_run"] = target_run

    # Default base to the run immediately OLDER than target.
    if not base:
        if target in run_ids:
            idx = run_ids.index(target)
            if idx + 1 < len(run_ids):
                base = run_ids[idx + 1]
            else:
                ctx["no_earlier"] = True
        # else: target exists in db but not in list (shouldn't happen) -> none

    base_run = None
    if base:
        base_run = db.get_run(project["id"], base)
        if base_run is None:
            ctx["error"] = f"Unknown run “{base}”."
            return _templates.TemplateResponse(
                request, "compare.html", ctx,
                status_code=status.HTTP_404_NOT_FOUND,
            )
    ctx["base"] = base or None
    ctx["base_run"] = base_run

    # Load unit results from storage.
    target_units = _load_units(target_run["json_key"])
    base_units = {} if base_run is None else _load_units(base_run["json_key"])

    if target_units is None or (base_run is not None and base_units is None):
        ctx["error"] = ("Could not load one of the run result files from "
                        "storage. The comparison is unavailable.")
        return _templates.TemplateResponse(request, "compare.html", ctx)

    base_units = base_units or {}

    rows = []
    added = removed = changed = 0
    for unit in set(base_units) | set(target_units):
        in_base = unit in base_units
        in_target = unit in target_units
        b_status = base_units.get(unit)
        t_status = target_units.get(unit)
        change = _classify(b_status if in_base else None,
                           t_status if in_target else None)
        if change == "same":
            continue
        if change == "added":
            added += 1
        elif change == "removed":
            removed += 1
        else:
            changed += 1
        rows.append({
            "unit": unit,
            "base_status": b_status if in_base else None,
            "target_status": t_status if in_target else None,
            "change": change,
        })

    rows.sort(key=_sort_key)
    ctx["rows"] = rows
    ctx["summary"] = {
        "added": added, "removed": removed, "changed": changed,
        "total": len(rows),
    }
    return _templates.TemplateResponse(request, "compare.html", ctx)
