"""Unit tests for ingestion.loaders.s3_client — env parsing and client wiring.

No network and no MinIO: these assert the *configuration* is what MinIO needs
(path-style addressing, SigV4, the configured endpoint), which is exactly the
class of mistake that only shows up as an unresolvable-host error in production.
"""

from __future__ import annotations

import pytest

from ingestion.loaders.s3_client import (
    DEFAULT_REGION,
    S3ConfigError,
    S3Settings,
    build_s3_client,
    load_s3_settings,
)

FULL_ENV = {
    "S3_ENDPOINT_URL": "http://storage-node:9000",
    "S3_ACCESS_KEY_ID": "minio-access",
    "S3_SECRET_ACCESS_KEY": "minio-secret",
    "S3_BUCKET_NAME": "uoip",
    "S3_REGION": "ca-central-1",
}


# ── load_s3_settings ─────────────────────────────────────────────────────────


def test_load_reads_all_fields_from_env():
    settings = load_s3_settings(FULL_ENV)

    assert settings.endpoint_url == "http://storage-node:9000"
    assert settings.access_key_id == "minio-access"
    assert settings.secret_access_key == "minio-secret"
    assert settings.bucket_name == "uoip"
    assert settings.region == "ca-central-1"


def test_load_defaults_region_when_absent():
    env = {k: v for k, v in FULL_ENV.items() if k != "S3_REGION"}

    assert load_s3_settings(env).region == DEFAULT_REGION


def test_load_defaults_region_when_blank():
    assert load_s3_settings({**FULL_ENV, "S3_REGION": "   "}).region == DEFAULT_REGION


def test_load_strips_surrounding_whitespace():
    """A trailing space in .env would otherwise become part of the hostname."""
    env = {**FULL_ENV, "S3_ENDPOINT_URL": "  http://storage-node:9000  "}

    assert load_s3_settings(env).endpoint_url == "http://storage-node:9000"


@pytest.mark.parametrize("var", sorted(set(FULL_ENV) - {"S3_REGION"}))
def test_load_raises_when_a_required_var_is_missing(var):
    env = {k: v for k, v in FULL_ENV.items() if k != var}

    with pytest.raises(S3ConfigError, match=var):
        load_s3_settings(env)


def test_load_treats_blank_as_missing():
    """An empty value in .env is the common case, not an absent key."""
    with pytest.raises(S3ConfigError, match="S3_SECRET_ACCESS_KEY"):
        load_s3_settings({**FULL_ENV, "S3_SECRET_ACCESS_KEY": ""})


def test_load_reports_every_missing_var_at_once():
    """A half-filled .env should not take four runs to diagnose."""
    with pytest.raises(S3ConfigError) as exc:
        load_s3_settings({"S3_ENDPOINT_URL": "http://storage-node:9000"})

    message = str(exc.value)
    assert "S3_ACCESS_KEY_ID" in message
    assert "S3_SECRET_ACCESS_KEY" in message
    assert "S3_BUCKET_NAME" in message


def test_load_falls_back_to_os_environ(monkeypatch):
    for key, value in FULL_ENV.items():
        monkeypatch.setenv(key, value)

    assert load_s3_settings().bucket_name == "uoip"


def test_repr_redacts_credentials():
    """Settings land in log lines and tracebacks; keys must not ride along."""
    rendered = repr(load_s3_settings(FULL_ENV))

    assert "minio-secret" not in rendered
    assert "minio-access" not in rendered
    assert "storage-node" in rendered


# ── build_s3_client ──────────────────────────────────────────────────────────


def test_client_uses_path_style_addressing():
    """MinIO has no virtual-host DNS — without path style every call fails."""
    client = build_s3_client(S3Settings(**_settings_kwargs()))

    assert client.meta.config.s3["addressing_style"] == "path"


def test_client_targets_the_configured_endpoint():
    client = build_s3_client(S3Settings(**_settings_kwargs()))

    assert client.meta.endpoint_url == "http://storage-node:9000"


def test_client_uses_sigv4():
    client = build_s3_client(S3Settings(**_settings_kwargs()))

    assert client.meta.config.signature_version == "s3v4"


def test_client_loads_settings_from_env_when_omitted(monkeypatch):
    for key, value in FULL_ENV.items():
        monkeypatch.setenv(key, value)

    assert build_s3_client().meta.endpoint_url == "http://storage-node:9000"


def _settings_kwargs() -> dict[str, str]:
    return {
        "endpoint_url": "http://storage-node:9000",
        "access_key_id": "minio-access",
        "secret_access_key": "minio-secret",
        "bucket_name": "uoip",
    }
