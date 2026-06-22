"""Object-storage abstraction for report payloads.

Backends are selected by ``TERRAKETTLE_STORAGE_BACKEND`` and loaded lazily so
that only the chosen cloud SDK needs to be installed. ``local`` requires no
extra dependency and is intended for development.

Each backend implements ``put``/``get``/``delete``; cloud backends also
implement ``presign`` so the web layer can hand clients a short-lived direct
URL instead of proxying the bytes. ``presign`` returns ``None`` when the
backend cannot sign (e.g. local disk), and the caller falls back to proxying.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from .config import Settings, get_settings


class Storage(ABC):
    """Stores and retrieves report files by key."""

    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str) -> None: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    def presign(self, key: str, expires: int) -> Optional[str]:
        """Return a short-lived direct URL, or None if unsupported."""
        return None

    def full_key(self, *parts: str) -> str:
        prefix = get_settings().storage_prefix.strip("/")
        joined = "/".join(p.strip("/") for p in parts if p)
        return f"{prefix}/{joined}" if prefix else joined


class LocalStorage(Storage):
    def __init__(self, settings: Settings):
        self.root = Path(settings.storage_bucket)

    def _path(self, key: str) -> Path:
        return self.root / key

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        path = self._path(key)
        path.unlink(missing_ok=True)
        # Tidy up the now-empty run directory (object stores have no dirs).
        parent = path.parent
        try:
            if parent != self.root and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass


class S3Storage(Storage):
    def __init__(self, settings: Settings):
        import boto3  # lazy: only needed for the s3 backend

        self.bucket = settings.storage_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
        )

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
        )

    def get(self, key: str) -> bytes:
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def presign(self, key: str, expires: int) -> Optional[str]:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires,
        )


class AzureStorage(Storage):
    def __init__(self, settings: Settings):
        from azure.storage.blob import BlobServiceClient  # lazy

        self._account_key = None
        self._account_name = None
        if settings.azure_connection_string:
            svc = BlobServiceClient.from_connection_string(
                settings.azure_connection_string
            )
            self._account_key = getattr(svc.credential, "account_key", None)
            self._account_name = svc.account_name
        elif settings.azure_account_url:
            from azure.identity import DefaultAzureCredential

            svc = BlobServiceClient(
                settings.azure_account_url, credential=DefaultAzureCredential()
            )
        else:
            raise RuntimeError(
                "Azure backend needs TERRAKETTLE_AZURE_CONNECTION_STRING "
                "or TERRAKETTLE_AZURE_ACCOUNT_URL"
            )
        self._svc = svc
        self._container_name = settings.storage_bucket
        self.container = svc.get_container_client(settings.storage_bucket)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        from azure.storage.blob import ContentSettings

        self.container.upload_blob(
            name=key, data=data, overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )

    def get(self, key: str) -> bytes:
        return self.container.download_blob(key).readall()

    def delete(self, key: str) -> None:
        self.container.delete_blob(key)

    def presign(self, key: str, expires: int) -> Optional[str]:
        # SAS requires a shared key; unavailable with DefaultAzureCredential.
        if not (self._account_key and self._account_name):
            return None
        from datetime import datetime, timedelta, timezone

        from azure.storage.blob import (BlobSasPermissions,
                                        generate_blob_sas)

        sas = generate_blob_sas(
            account_name=self._account_name,
            container_name=self._container_name,
            blob_name=key,
            account_key=self._account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(timezone.utc) + timedelta(seconds=expires),
        )
        return f"{self.container.url}/{key}?{sas}"


class GCSStorage(Storage):
    def __init__(self, settings: Settings):
        from google.cloud import storage as gcs  # lazy

        self.bucket = gcs.Client().bucket(settings.storage_bucket)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.bucket.blob(key).upload_from_string(data, content_type=content_type)

    def get(self, key: str) -> bytes:
        return self.bucket.blob(key).download_as_bytes()

    def delete(self, key: str) -> None:
        self.bucket.blob(key).delete()

    def presign(self, key: str, expires: int) -> Optional[str]:
        from datetime import timedelta

        try:
            return self.bucket.blob(key).generate_signed_url(
                version="v4", expiration=timedelta(seconds=expires), method="GET"
            )
        except Exception:
            # No signing credentials (e.g. metadata-server identity).
            return None


_BACKENDS = {
    "local": LocalStorage,
    "s3": S3Storage,
    "azure": AzureStorage,
    "gcs": GCSStorage,
}

_instance: Optional[Storage] = None


def get_storage() -> Storage:
    global _instance
    if _instance is None:
        settings = get_settings()
        backend = settings.storage_backend.lower()
        if backend not in _BACKENDS:
            raise RuntimeError(f"Unknown storage backend: {backend}")
        _instance = _BACKENDS[backend](settings)
    return _instance
