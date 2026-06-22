"""Authentication: push tokens, admin key, and view sessions.

- **Admin key** — bearer guard for project/token management.
- **Push tokens** — per-project opaque bearer tokens, optionally expiring and
  rate-limited, used by Terrahawk to publish reports.
- **View sessions** — signed cookies issued after password login, gating the
  web UI (enforced by middleware in ``app.py``).
"""

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Header, HTTPException, status

from . import db
from .config import get_settings

_TOKEN_PREFIX = "tk_"

# In-memory sliding-window push counters: token_hash -> deque[monotonic ts].
# Per-process; adequate for single-instance deployments.
_push_hits: dict = defaultdict(deque)


def generate_token(slug: str) -> str:
    """Mint a new opaque push token for a project."""
    return f"{_TOKEN_PREFIX}{slug}_{secrets.token_urlsafe(32)}"


def token_expiry(ttl_days: int) -> Optional[str]:
    """ISO expiry for a freshly minted token, or None if it never expires."""
    if ttl_days <= 0:
        return None
    return (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _bearer(authorization: str) -> str:
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Bearer token required"
        )
    return value.strip()


def require_admin(authorization: str = Header(default="")) -> None:
    """Guard for project/token management endpoints."""
    token = _bearer(authorization)
    if not hmac.compare_digest(token, get_settings().admin_key):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid admin key")


def _rate_limit(token_hash: str) -> None:
    limit = get_settings().push_rate_per_min
    if limit <= 0:
        return
    now = time.monotonic()
    hits = _push_hits[token_hash]
    while hits and hits[0] < now - 60:
        hits.popleft()
    if len(hits) >= limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Rate limit: {limit} pushes/min",
        )
    hits.append(now)


def require_project(authorization: str = Header(default="")):
    """Resolve the project a (valid, non-expired) push token is scoped to."""
    token = _bearer(authorization)
    th = hash_token(token)
    project = db.project_for_token(th)
    if project is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Invalid or expired push token")
    _rate_limit(th)
    return project


# --- View sessions ----------------------------------------------------------

def _sign(msg: str) -> str:
    secret = get_settings().effective_session_secret.encode()
    sig = hmac.new(secret, msg.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode().rstrip("=")


def make_session() -> str:
    """Create a signed session token valid for ``session_ttl`` seconds."""
    exp = str(int(time.time()) + get_settings().session_ttl)
    return f"{exp}.{_sign(exp)}"


def verify_session(cookie: Optional[str]) -> bool:
    if not cookie or "." not in cookie:
        return False
    exp, _, sig = cookie.partition(".")
    if not hmac.compare_digest(sig, _sign(exp)):
        return False
    try:
        return int(exp) >= int(time.time())
    except ValueError:
        return False


def check_view_password(password: str) -> bool:
    expected = get_settings().view_password
    return bool(expected) and hmac.compare_digest(password, expected)


def view_auth_enabled() -> bool:
    return bool(get_settings().view_password)
