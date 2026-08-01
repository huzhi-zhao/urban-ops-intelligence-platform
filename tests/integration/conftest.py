"""
Shared fixtures for integration tests — they run against a real MinIO instance.

Unlike the cloud setup these replaced, MinIO can be stood up locally, so these
tests do a genuine round-trip (upload → download → decompress → compare) rather
than trusting a mock. That round-trip is the point: the one failure mode gzip
introduced (see ADR 0006 §4.1) is invisible to a mock and only shows up when
something actually reads the bytes back.

Every test here skips unless the S3_* variables are set, so `make test-unit`
stays runnable with no infrastructure.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ingestion.loaders.s3_client import (  # noqa: E402
    S3ConfigError,
    S3Settings,
    build_s3_client,
    load_s3_settings,
)


@pytest.fixture(scope="session")
def s3_settings() -> S3Settings:
    """Object-storage settings, or skip the whole module if unconfigured."""
    try:
        return load_s3_settings()
    except S3ConfigError as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="session")
def s3_client(s3_settings: S3Settings) -> Any:
    """A boto3 client verified to actually reach the configured bucket.

    Reaching the bucket is checked once per session: a wrong endpoint or a
    missing bucket should skip with one clear message, not fail every test with
    a different botocore error.
    """
    client = build_s3_client(s3_settings)
    try:
        client.head_bucket(Bucket=s3_settings.bucket_name)
    except Exception as exc:  # noqa: BLE001 — any failure to reach it means skip
        pytest.skip(f"Cannot reach bucket {s3_settings.bucket_name!r}: {exc}")
    return client


@pytest.fixture(scope="session")
def bucket(s3_settings: S3Settings) -> str:
    return s3_settings.bucket_name


def object_exists(client: Any, bucket_name: str, key: str) -> bool:
    from botocore.exceptions import ClientError

    try:
        client.head_object(Bucket=bucket_name, Key=key)
    except ClientError:
        return False
    return True


def read_bytes(client: Any, bucket_name: str, key: str) -> bytes:
    """Fetch an object, transparently gunzipping it when the key says .gz."""
    raw = client.get_object(Bucket=bucket_name, Key=key)["Body"].read()
    return gzip.decompress(raw) if key.endswith(".gz") else raw


def read_ndjson(client: Any, bucket_name: str, key: str) -> list[dict[str, Any]]:
    text = read_bytes(client, bucket_name, key).decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def read_json(client: Any, bucket_name: str, key: str) -> Any:
    return json.loads(read_bytes(client, bucket_name, key))
