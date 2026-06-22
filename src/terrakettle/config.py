"""Runtime configuration, sourced from environment variables.

All settings carry a ``TERRAKETTLE_`` prefix, e.g. ``TERRAKETTLE_ADMIN_KEY``.
"""

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TERRAKETTLE_", env_file=".env")

    # --- Core ---------------------------------------------------------------
    # Admin key guards project/token management endpoints. Required in prod.
    admin_key: str = "change-me"
    # SQLite file holding the projects/tokens/runs metadata index.
    db_path: str = "terrakettle.db"
    # Max upload size per file (bytes). Default 64 MiB.
    max_upload_bytes: int = 64 * 1024 * 1024
    # Default page size for paginated listings.
    page_size: int = 50
    # Set true to allow the insecure default admin key / open viewing. When
    # false (default) the app refuses to start with an unchanged admin key.
    insecure: bool = False

    # --- View authentication ------------------------------------------------
    # When set, the web UI requires login with this password (a signed session
    # cookie is issued). When empty, viewing is open — only acceptable behind a
    # trusted network or when `insecure=true`.
    view_password: str = ""
    # Secret used to sign session cookies. Auto-derived from admin_key if unset.
    session_secret: str = ""
    # Session lifetime in seconds (default 7 days).
    session_ttl: int = 7 * 24 * 3600

    # --- Push tokens --------------------------------------------------------
    # Optional token lifetime in days (0 = never expires).
    token_ttl_days: int = 0
    # Max pushes per token per minute (0 = unlimited).
    push_rate_per_min: int = 0

    # --- Notifications ------------------------------------------------------
    # Incoming webhook URL (Slack/Teams/generic) called when a pushed run has
    # drift or errors. Empty disables notifications.
    notify_webhook: str = ""
    # Webhook flavor: slack | teams | generic
    notify_format: str = "slack"
    # Public base URL of this server, used to build links in notifications.
    public_url: str = ""

    # --- Security headers ---------------------------------------------------
    # Content-Security-Policy applied when serving stored report HTML. The
    # default permits the inline scripts and jsDelivr CDN that Terrahawk
    # reports require, while forbidding framing. For strong isolation, serve
    # reports from a separate origin (see README).
    report_csp: str = (
        "default-src 'self' 'unsafe-inline' 'unsafe-eval' "
        "https://cdn.jsdelivr.net data:; frame-ancestors 'none'"
    )

    # --- Retention ----------------------------------------------------------
    # Keep at most this many runs per project (oldest pruned on push).
    # 0 disables automatic pruning; the `prune` CLI still works.
    max_runs_per_project: int = 0

    # --- Serving ------------------------------------------------------------
    # Redirect report sidecar files (data.js / json) to presigned object-store
    # URLs when the backend supports it, instead of proxying the bytes.
    signed_urls: bool = True
    # Presigned URL lifetime in seconds.
    signed_url_ttl: int = 300

    # --- Storage ------------------------------------------------------------
    # Backend for report payloads: local | s3 | azure | gcs
    storage_backend: str = "local"
    # Common: bucket/container name (s3/gcs/azure) or base dir (local).
    storage_bucket: str = "terrakettle_data"
    # Key prefix inside the bucket/container.
    storage_prefix: str = "reports"

    # S3 / S3-compatible (MinIO) overrides — credentials come from the
    # standard AWS env/credential chain unless set here.
    s3_endpoint_url: Optional[str] = None
    s3_region: Optional[str] = None

    # Azure Blob — connection string or account URL + default credential.
    azure_connection_string: Optional[str] = None
    azure_account_url: Optional[str] = None

    @property
    def effective_session_secret(self) -> str:
        return self.session_secret or f"sess::{self.admin_key}"

    def validate_startup(self) -> list[str]:
        """Return fatal misconfig messages (empty = OK to start)."""
        errors: list[str] = []
        if not self.insecure:
            if self.admin_key == "change-me":
                errors.append(
                    "TERRAKETTLE_ADMIN_KEY is the insecure default. Set a real "
                    "key, or set TERRAKETTLE_INSECURE=true to override."
                )
        return errors


@lru_cache
def get_settings() -> Settings:
    return Settings()
