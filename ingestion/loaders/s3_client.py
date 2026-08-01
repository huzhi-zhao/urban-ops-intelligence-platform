"""
S3 client construction for the self-hosted MinIO object store.

Everything that talks to Bronze goes through :func:`build_s3_client`, so the
MinIO-specific settings live in exactly one place rather than being repeated at
each call site.

Configuration is environment-only (see ``.env.example``); no credential ever
appears in a tracked file, in a CLI flag, or in a Spark ``--conf`` value.

    S3_ENDPOINT_URL       http://<storage-node>:9000
    S3_ACCESS_KEY_ID      MinIO access key
    S3_SECRET_ACCESS_KEY  MinIO secret key
    S3_BUCKET_NAME        bucket holding bronze/ silver/ gold/  (currently "uoip")
    S3_REGION             optional; MinIO ignores it but SigV4 requires *a* value

The bucket name is a *deployment* setting rather than a client setting, so it is
carried on :class:`S3Settings` but never baked into the client — loaders and the
backfill CLI take it as an explicit argument.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

DEFAULT_REGION = "us-east-1"

# Required for MinIO: it has no virtual-host-style DNS, so bucket names must be
# sent in the path (http://host:9000/bucket/key) rather than as a subdomain
# (http://bucket.host:9000/key). Without this every request fails to resolve.
ADDRESSING_STYLE = "path"


@dataclass(frozen=True)
class S3Settings:
    """Connection settings for the Bronze/Silver/Gold object store."""

    endpoint_url: str
    access_key_id: str
    secret_access_key: str
    bucket_name: str
    region: str = DEFAULT_REGION

    def __repr__(self) -> str:
        """Redacted repr — these objects end up in log lines and tracebacks."""
        return (
            f"S3Settings(endpoint_url={self.endpoint_url!r}, "
            f"bucket_name={self.bucket_name!r}, region={self.region!r}, "
            f"access_key_id=***, secret_access_key=***)"
        )


class S3ConfigError(OSError):
    """Raised when required S3 environment variables are missing."""


_REQUIRED_VARS = (
    ("endpoint_url", "S3_ENDPOINT_URL"),
    ("access_key_id", "S3_ACCESS_KEY_ID"),
    ("secret_access_key", "S3_SECRET_ACCESS_KEY"),
    ("bucket_name", "S3_BUCKET_NAME"),
)


def load_s3_settings(env: dict[str, str] | None = None) -> S3Settings:
    """Read :class:`S3Settings` from the environment.

    Args:
        env: Mapping to read from. Defaults to ``os.environ``.

    Raises:
        S3ConfigError: if any required variable is missing or blank. All
            missing names are reported at once — a half-configured ``.env``
            should not require four runs to diagnose.
    """
    source = os.environ if env is None else env

    values: dict[str, str] = {}
    missing: list[str] = []
    for field, var in _REQUIRED_VARS:
        value = (source.get(var) or "").strip()
        if not value:
            missing.append(var)
        values[field] = value

    if missing:
        raise S3ConfigError(
            f"Missing required object-storage environment variable(s): "
            f"{', '.join(missing)}. See .env.example.",
        )

    return S3Settings(
        region=(source.get("S3_REGION") or "").strip() or DEFAULT_REGION,
        **values,
    )


def build_s3_client(settings: S3Settings | None = None) -> Any:
    """Construct a boto3 S3 client pointed at MinIO.

    Args:
        settings: Connection settings. Loaded from the environment when omitted.

    Returns:
        A ``boto3`` S3 client. Typed as ``Any`` because botocore builds its
        client classes at runtime and exposes no static type for them.
    """
    import boto3
    from botocore.config import Config

    cfg = settings or load_s3_settings()

    return boto3.client(
        "s3",
        endpoint_url=cfg.endpoint_url,
        aws_access_key_id=cfg.access_key_id,
        aws_secret_access_key=cfg.secret_access_key,
        region_name=cfg.region,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": ADDRESSING_STYLE},
            # The upstream APIs are the flaky part of this pipeline, not MinIO on
            # the LAN; a short adaptive retry keeps a transient blip from failing
            # a whole backfill slice without masking a genuinely down store.
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )
