"""Run pruning: delete report payloads from storage and their index rows."""

from datetime import datetime, timedelta, timezone

from . import db
from .storage import get_storage


def _delete_run(run) -> None:
    """Remove a run's stored files, then its metadata row."""
    storage = get_storage()
    for key in (run["html_key"], run["data_js_key"], run["json_key"]):
        if key:
            try:
                storage.delete(key)
            except Exception:
                pass  # best-effort; still drop the index row
    db.delete_run_row(run["id"])


def prune_project(project_id: int, keep: int) -> int:
    """Keep only the newest ``keep`` runs of a project. Returns count pruned."""
    victims = db.runs_beyond(project_id, keep)
    for run in victims:
        _delete_run(run)
    return len(victims)


def prune_all_keep(keep: int) -> int:
    total = 0
    for pid in db.all_project_ids():
        total += prune_project(pid, keep)
    return total


def prune_older_than(days: int) -> int:
    """Prune runs older than ``days`` across all projects."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    victims = db.runs_older_than(cutoff)
    for run in victims:
        _delete_run(run)
    return len(victims)
