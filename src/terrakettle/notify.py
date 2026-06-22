"""Best-effort outbound notifications for report-push runs.

Sends a webhook notification (Slack, Teams, or generic JSON) when a run has
drift or errors. Notifications are fire-and-forget: they run on a daemon thread
and never raise or block the caller.
"""

from __future__ import annotations

import json
import threading
import urllib.request

from .config import get_settings

_TIMEOUT = 10


def _build_message(slug: str, run_id: str, summary: dict) -> str:
    """Build the human-readable notification message."""
    return (
        f"Terrahawk: {slug} run {run_id} — "
        f"{summary['drift']} drift, {summary['error']} error, "
        f"{summary['clean']} clean / {summary['total']} units"
    )


def _build_payload(
    fmt: str,
    slug: str,
    run_id: str,
    summary: dict,
    report_url: str | None,
    message: str,
) -> dict:
    """Build the format-specific JSON payload."""
    if fmt == "slack":
        text = message
        if report_url:
            text = f"{text} <{report_url}|View report>"
        return {"text": text}

    if fmt == "teams":
        text = message
        if report_url:
            text = f"{text}\n\n[View report]({report_url})"
        theme_color = "FF0000" if summary["error"] > 0 else "FFA500"
        return {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "summary": f"Terrahawk: {slug} run {run_id}",
            "themeColor": theme_color,
            "title": f"Terrahawk: {slug}",
            "text": text,
        }

    # generic
    return {
        "project": slug,
        "run_id": run_id,
        "summary": summary,
        "report_url": report_url,
        "message": message,
    }


def _post(webhook: str, payload: dict) -> None:
    """POST the JSON payload to the webhook, swallowing all errors."""
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=_TIMEOUT)  # noqa: S310
    except Exception as exc:  # noqa: BLE001 - notifications must never raise
        print(f"warning: notification webhook failed: {exc}")


def maybe_notify(
    slug: str, run_id: str, summary: dict, report_url: str | None
) -> None:
    """Maybe send a notification for a completed run.

    No-op unless a webhook is configured and the run had drift or errors.
    Never raises and never blocks: the POST runs on a daemon thread.
    """
    settings = get_settings()

    webhook = settings.notify_webhook
    if not webhook:
        return

    if not (summary["drift"] > 0 or summary["error"] > 0):
        return

    message = _build_message(slug, run_id, summary)
    payload = _build_payload(
        settings.notify_format, slug, run_id, summary, report_url, message
    )

    thread = threading.Thread(
        target=_post, args=(webhook, payload), daemon=True
    )
    thread.start()
