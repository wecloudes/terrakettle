"""SVG status badge endpoint (GitHub shields flat style)."""

from html import escape

from fastapi import APIRouter, HTTPException, Response

from . import db

router = APIRouter()

LABEL = "terrahawk"
GREY = "#9f9f9f"
RED = "#e05d44"
ORANGE = "#fe7d37"
GREEN = "#4c1"


def _text_width(text: str) -> int:
    """Approximate rendered width in px for the badge font."""
    return int(len(text) * 6.5) + 10


def _render_badge(label: str, message: str, color: str) -> str:
    label = str(label)
    message = str(message)

    label_w = _text_width(label)
    message_w = _text_width(message)
    total_w = label_w + message_w

    # Center positions (x10 scale used by the shields textLength trick).
    label_cx = label_w * 10 // 2
    message_cx = label_w * 10 + (message_w * 10 // 2)
    label_tl = (label_w - 10) * 10
    message_tl = (message_w - 10) * 10

    e_label = escape(label)
    e_message = escape(message)
    title = escape(f"{label}: {message}")

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{total_w}" height="20" role="img" '
        f'aria-label="{title}">'
        f'<title>{title}</title>'
        f'<linearGradient id="s" x2="0" y2="100%">'
        f'<stop offset="0" stop-color="#bbb" stop-opacity=".1"/>'
        f'<stop offset="1" stop-opacity=".1"/>'
        f'</linearGradient>'
        f'<clipPath id="r"><rect width="{total_w}" height="20" rx="3" '
        f'fill="#fff"/></clipPath>'
        f'<g clip-path="url(#r)">'
        f'<rect width="{label_w}" height="20" fill="#555"/>'
        f'<rect x="{label_w}" width="{message_w}" height="20" '
        f'fill="{color}"/>'
        f'<rect width="{total_w}" height="20" fill="url(#s)"/>'
        f'</g>'
        f'<g fill="#fff" text-anchor="middle" '
        f'font-family="Verdana,Geneva,DejaVu Sans,sans-serif" '
        f'text-rendering="geometricPrecision" font-size="110">'
        f'<text aria-hidden="true" x="{label_cx}" y="150" fill="#010101" '
        f'fill-opacity=".3" transform="scale(.1)" textLength="{label_tl}">'
        f'{e_label}</text>'
        f'<text x="{label_cx}" y="140" transform="scale(.1)" '
        f'fill="#fff" textLength="{label_tl}">{e_label}</text>'
        f'<text aria-hidden="true" x="{message_cx}" y="150" fill="#010101" '
        f'fill-opacity=".3" transform="scale(.1)" textLength="{message_tl}">'
        f'{e_message}</text>'
        f'<text x="{message_cx}" y="140" transform="scale(.1)" '
        f'fill="#fff" textLength="{message_tl}">{e_message}</text>'
        f'</g>'
        f'</svg>'
    )


@router.get("/p/{slug}/badge.svg")
def badge(slug: str) -> Response:
    project = db.get_project(slug)
    if project is None:
        raise HTTPException(404)

    runs = db.list_runs(project["id"], limit=1)

    if not runs:
        message, color = "no data", GREY
    else:
        latest = runs[0]
        error = latest["error"] or 0
        drift = latest["drift"] or 0
        if error > 0:
            message, color = f"{error} error", RED
        elif drift > 0:
            message, color = f"{drift} drift", ORANGE
        else:
            message, color = "clean", GREEN

    svg = _render_badge(LABEL, message, color)
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "max-age=60"},
    )
