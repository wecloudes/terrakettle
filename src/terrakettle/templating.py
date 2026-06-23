"""Shared Jinja2 templates with global context (header, about modal).

Both ``web.py`` and ``compare.py`` render pages that extend ``base.html``,
which references globals like ``version``/``storage_backend``/``about``. Sharing
one configured ``Jinja2Templates`` keeps those globals available to every page
regardless of which module renders it.
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from .__init__ import __version__
from .config import get_settings

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_s = get_settings()
templates.env.globals["version"] = __version__
templates.env.globals["storage_backend"] = _s.resolved_backend()
templates.env.globals["about"] = {
    "version": __version__,
    "storage_backend": _s.resolved_backend(),
    "signed_urls": _s.signed_urls,
    "view_auth": bool(_s.view_password),
    "max_runs": _s.max_runs_per_project,
    "notify": bool(_s.notify_webhook),
}
