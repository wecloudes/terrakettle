"""Pydantic response models and report-summary helpers."""

import re
from typing import Optional

from pydantic import BaseModel

_RUN_TS_RE = re.compile(r"(\d{8})_(\d{6})")


class ProjectIn(BaseModel):
    slug: str
    name: Optional[str] = None


class ProjectOut(BaseModel):
    slug: str
    name: str


class TokenOut(BaseModel):
    token: str
    project: str
    note: str = "Store this now — it is not retrievable later."


class RunOut(BaseModel):
    run_id: str
    report_date: Optional[str] = None
    total: int
    clean: int
    drift: int
    error: int
    timeout: int
    report_url: str
    json_url: str


def summarize(results: list) -> dict:
    """Count units by status from a Terrahawk results array."""
    counts = {"total": len(results), "clean": 0, "drift": 0,
              "error": 0, "timeout": 0}
    for r in results:
        status = r.get("status")
        if status in counts:
            counts[status] += 1
    return counts


def report_date_from_run_id(run_id: str) -> Optional[str]:
    """Derive an ISO-ish date from a ``terrahawk_YYYYMMDD_HHMMSS`` name."""
    m = _RUN_TS_RE.search(run_id)
    if not m:
        return None
    d, t = m.group(1), m.group(2)
    return f"{d[:4]}-{d[4:6]}-{d[6:8]} {t[:2]}:{t[2:4]}:{t[4:6]}"
