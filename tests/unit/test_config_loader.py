"""
Unit tests for ingestion.config (YAML source registry loader).

Coverage targets:
- Happy path: all 4 source YAMLs load and validate, with field assertions
  specific to each source's api_type.
- Error branches: corrupt YAML, missing required fields, extra=forbid,
  regex violations on id/priority/name, empty datasets, wrong field
  combinations per api_type, unknown source id, env var override.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ingestion.config import (  # noqa: E402
    CONFIG_DIR_ENV_VAR,
    ApiType,
    ConfigLoadError,
    DatasetConfig,
    SourceConfig,
    SourceType,
    load_all_sources,
    load_source_config,
)

# ── Happy path: loading the committed YAMLs ──────────────────────────────────


EXPECTED_SOURCE_IDS = {
    "SRC-NYC-311",
    "SRC-NYPD",
    "SRC-Open-Meteo",
    "SRC-DCP",
    "SRC-WPG-SNOW",
}


def test_load_all_sources_returns_every_committed_source():
    sources = load_all_sources()
    assert set(sources) == EXPECTED_SOURCE_IDS
    for cfg in sources.values():
        assert isinstance(cfg, SourceConfig)


def test_311_source_metadata():
    cfg = load_source_config("SRC-NYC-311")
    assert cfg.source.id == "SRC-NYC-311"
    assert cfg.source.name == "NYC 311 Service Requests"
    assert cfg.source.type == SourceType.REST_API_SOCRATA
    assert cfg.source.priority == "P0"
    assert cfg.source.status == "production"
    assert cfg.source.owner == "city_operations"
    assert cfg.source.partition_strategy == "daily"


def test_311_dataset_socrata_fields():
    cfg = load_source_config("SRC-NYC-311")
    assert len(cfg.datasets) == 1
    ds = cfg.datasets[0]
    assert ds.name == "nyc_311"
    assert ds.api_type == ApiType.SOCRATA
    assert ds.resource_id == "erm2-nwe9"
    assert ds.domain == "data.cityofnewyork.us"
    assert ds.timestamp_field == "created_date"
    # Negative: fields that socrata must not have
    assert ds.endpoint is None
    assert ds.format is None
    assert ds.query_params is None


def test_nypd_has_four_datasets_with_distinct_resource_ids():
    cfg = load_source_config("SRC-NYPD")
    assert len(cfg.datasets) == 4
    resource_ids = {d.resource_id for d in cfg.datasets}
    assert len(resource_ids) == 4, "Each NYPD dataset must have a unique resource_id"

    names = [d.name for d in cfg.datasets]
    assert names == [
        "nypd_collisions",
        "nypd_complaint_historic",
        "nypd_complaint_current",
        "nypd_shooting_incident",
    ]
    for d in cfg.datasets:
        assert d.api_type == ApiType.SOCRATA
        assert d.domain == "data.cityofnewyork.us"


@pytest.mark.parametrize(
    "dataset_name,expected_field",
    [
        ("nypd_collisions", "crash_date"),
        ("nypd_complaint_historic", "cmplnt_fr_dt"),
        ("nypd_complaint_current", "cmplnt_fr_dt"),
        ("nypd_shooting_incident", "occur_date"),
    ],
)
def test_nypd_dataset_timestamp_fields_distinct(dataset_name, expected_field):
    cfg = load_source_config("SRC-NYPD")
    ds = next(d for d in cfg.datasets if d.name == dataset_name)
    assert ds.timestamp_field == expected_field


def test_open_meteo_uses_endpoint_and_query_params():
    cfg = load_source_config("SRC-Open-Meteo")
    ds = cfg.datasets[0]
    assert ds.name == "nyc_weather_forecast"
    assert ds.api_type == ApiType.OPEN_METEO
    assert ds.endpoint == "https://api.open-meteo.com/v1/forecast"
    assert ds.timestamp_field == "time"
    # Query params are preserved as a dict
    assert ds.query_params is not None
    assert ds.query_params["latitude"] == 40.7143
    assert ds.query_params["longitude"] == -74.006
    assert "hourly" in ds.query_params
    assert ds.query_params["timezone"] == "America/New_York"
    # Open-Meteo must NOT have socrata fields
    assert ds.resource_id is None
    assert ds.domain is None
    assert ds.format is None
    assert cfg.source.partition_strategy == "daily"


def test_dcp_is_static_geojson():
    cfg = load_source_config("SRC-DCP")
    ds = cfg.datasets[0]
    assert ds.name == "borough_boundaries"
    assert ds.api_type == ApiType.SOCRATA_GEOJSON
    assert ds.format == "geojson"
    assert ds.resource_id == "gthc-hcne"
    assert ds.domain == "data.cityofnewyork.us"
    # Static dataset: no incremental timestamp field
    assert ds.timestamp_field is None
    assert ds.endpoint is None
    assert cfg.source.partition_strategy == "static"


def test_nypd_partition_strategy_is_monthly():
    cfg = load_source_config("SRC-NYPD")
    assert cfg.source.partition_strategy == "monthly"
    # All NYPD datasets keep their timestamp_field for the fetch window
    for d in cfg.datasets:
        assert d.timestamp_field, f"{d.name} should have a timestamp_field"


# ── partition_strategy validation ────────────────────────────────────────────


def test_daily_partition_requires_timestamp_field_on_every_dataset(isolated_config_dir):
    """Setting partition_strategy=daily without a timestamp_field is rejected."""
    (isolated_config_dir / "daily_missing_ts.yaml").write_text(
        "source:\n"
        "  id: SRC-TEST-001\n"
        "  name: daily-missing-ts\n"
        "  type: rest_api\n"
        "  owner: x\n"
        "  priority: P2\n"
        "  status: production\n"
        "  partition_strategy: daily\n"
        "datasets:\n"
        "  - name: foo\n"
        "    api_type: open_meteo\n"
        "    endpoint: https://x\n"
        # intentionally no timestamp_field
    )
    with pytest.raises(ConfigLoadError) as exc_info:
        load_all_sources()
    msg = str(exc_info.value)
    assert "partition_strategy='daily'" in msg or "daily" in msg
    assert "timestamp_field" in msg
    assert "foo" in msg


def test_invalid_partition_strategy_value_rejected(isolated_config_dir):
    """Unknown partition_strategy values are rejected by the Literal type."""
    (isolated_config_dir / "bad_partition.yaml").write_text(
        "source:\n"
        "  id: SRC-TEST-001\n"
        "  name: bad\n"
        "  type: rest_api\n"
        "  owner: x\n"
        "  priority: P2\n"
        "  status: production\n"
        "  partition_strategy: hourly\n"
        "datasets:\n"
        "  - name: foo\n"
        "    api_type: open_meteo\n"
        "    endpoint: https://x\n"
        "    timestamp_field: t\n"
    )
    with pytest.raises(ConfigLoadError) as exc_info:
        load_all_sources()
    assert "partition_strategy" in str(exc_info.value)


def test_partition_strategy_defaults_to_monthly_when_omitted(isolated_config_dir):
    """A YAML without partition_strategy falls back to 'monthly'."""
    (isolated_config_dir / "default.yaml").write_text(
        "source:\n"
        "  id: SRC-DEFAULT-001\n"
        "  name: default\n"
        "  type: rest_api_socrata\n"
        "  owner: x\n"
        "  priority: P2\n"
        "  status: production\n"
        "datasets:\n"
        "  - name: foo\n"
        "    api_type: socrata\n"
        "    resource_id: abc\n"
        "    domain: example.com\n"
        "    timestamp_field: t\n"
    )
    cfg = load_source_config("SRC-DEFAULT-001")
    assert cfg.source.partition_strategy == "monthly"


# ── Error branches: unknown source ───────────────────────────────────────────


def test_load_unknown_source_raises_with_path():
    with pytest.raises(ConfigLoadError) as exc_info:
        load_source_config("SRC-DOES-NOT-EXIST")
    msg = str(exc_info.value)
    assert "SRC-DOES-NOT-EXIST" in msg
    assert "config" in msg.lower()
    # Should list what IS available
    assert "SRC-NYC-311" in msg


# ── Error branches: corrupt / invalid YAML via env override ──────────────────


@pytest.fixture
def isolated_config_dir(tmp_path, monkeypatch):
    """Point NYC_UOIP_CONFIG_DIR at a fresh tmp_path for the duration of the test."""
    monkeypatch.setenv(CONFIG_DIR_ENV_VAR, str(tmp_path))
    return tmp_path


def test_load_all_sources_with_corrupt_yaml_raises(isolated_config_dir):
    (isolated_config_dir / "bad.yaml").write_text(
        "source:\n  id: SRC-FAKE-001\n  name: bad\n  type: rest_api\n"
        "  owner: x\n  priority: P3\n  status: production\n"
        "datasets:\n  - name: x\n    api_type: open_meteo\n"
        "    endpoint: https://x\n    :\n"  # invalid YAML
        "  - this is broken\n",
    )
    with pytest.raises(ConfigLoadError) as exc_info:
        load_all_sources()
    msg = str(exc_info.value)
    assert "bad.yaml" in msg
    assert "Corrupt YAML" in msg or "YAML" in msg


def test_load_all_sources_with_non_mapping_top_level_raises(isolated_config_dir):
    (isolated_config_dir / "list.yaml").write_text("- just\n- a\n- list\n")
    with pytest.raises(ConfigLoadError) as exc_info:
        load_all_sources()
    assert "list.yaml" in msg_safe(exc_info.value)
    assert "mapping" in str(exc_info.value).lower()


def msg_safe(exc):
    return str(exc)


def test_missing_required_field_on_socrata_dataset_raises(isolated_config_dir):
    """socrata dataset missing resource_id and domain."""
    (isolated_config_dir / "broken.yaml").write_text(
        "source:\n"
        "  id: SRC-TEST-001\n"
        "  name: broken\n"
        "  type: rest_api_socrata\n"
        "  owner: x\n"
        "  priority: P2\n"
        "  status: production\n"
        "datasets:\n"
        "  - name: foo\n"
        "    api_type: socrata\n"
        "    timestamp_field: created_date\n",
        # missing resource_id + domain
    )
    with pytest.raises(ConfigLoadError) as exc_info:
        load_all_sources()
    msg = str(exc_info.value)
    assert "broken.yaml" in msg
    assert "resource_id" in msg


def test_unknown_field_rejected_via_extra_forbid(isolated_config_dir):
    """extra='forbid' on SourceConfig should reject typo fields."""
    (isolated_config_dir / "typo.yaml").write_text(
        "source:\n"
        "  id: SRC-TEST-001\n"
        "  name: typo\n"
        "  type: rest_api_socrata\n"
        "  owner: x\n"
        "  priority: P2\n"
        "  status: production\n"
        "datasets:\n"
        "  - name: foo\n"
        "    api_type: socrata\n"
        "    resource_id: abc\n"
        "    domain: example.com\n"
        "    timestamp_field: t\n"
        "foo_bar: oops\n",  # unknown top-level
    )
    with pytest.raises(ConfigLoadError) as exc_info:
        load_all_sources()
    assert "foo_bar" in str(exc_info.value)


def test_invalid_source_id_pattern_rejected(isolated_config_dir):
    (isolated_config_dir / "badid.yaml").write_text(
        "source:\n"
        "  id: not-a-valid-id\n"  # doesn't match ^SRC-[A-Za-z0-9-]+$
        "  name: bad\n"
        "  type: rest_api\n"
        "  owner: x\n"
        "  priority: P0\n"
        "  status: production\n"
        "datasets:\n"
        "  - name: foo\n"
        "    api_type: open_meteo\n"
        "    endpoint: https://x\n"
    )
    with pytest.raises(ConfigLoadError) as exc_info:
        load_all_sources()
    assert "String should match pattern" in str(exc_info.value) or "pattern" in str(exc_info.value).lower()


def test_invalid_priority_rejected(isolated_config_dir):
    (isolated_config_dir / "badprio.yaml").write_text(
        "source:\n"
        "  id: SRC-TEST-001\n"
        "  name: bad\n"
        "  type: rest_api\n"
        "  owner: x\n"
        "  priority: P9\n"  # invalid
        "  status: production\n"
        "datasets:\n"
        "  - name: foo\n"
        "    api_type: open_meteo\n"
        "    endpoint: https://x\n"
    )
    with pytest.raises(ConfigLoadError):
        load_all_sources()


def test_invalid_status_rejected(isolated_config_dir):
    (isolated_config_dir / "badstat.yaml").write_text(
        "source:\n"
        "  id: SRC-TEST-001\n"
        "  name: bad\n"
        "  type: rest_api\n"
        "  owner: x\n"
        "  priority: P0\n"
        "  status: retired\n"  # not in Literal
        "datasets:\n"
        "  - name: foo\n"
        "    api_type: open_meteo\n"
        "    endpoint: https://x\n"
    )
    with pytest.raises(ConfigLoadError):
        load_all_sources()


def test_empty_datasets_rejected(isolated_config_dir):
    (isolated_config_dir / "empty.yaml").write_text(
        "source:\n"
        "  id: SRC-TEST-001\n"
        "  name: empty\n"
        "  type: rest_api\n"
        "  owner: x\n"
        "  priority: P0\n"
        "  status: production\n"
        "datasets: []\n",
    )
    with pytest.raises(ConfigLoadError):
        load_all_sources()


@pytest.mark.parametrize(
    "api_type,required,forbidden",
    [
        # socrata missing resource_id/domain
        ("socrata", "resource_id", None),
        # open_meteo missing endpoint
        ("open_meteo", "endpoint", None),
        # socrata with extra endpoint
        ("socrata", None, "endpoint"),
        # open_meteo with extra resource_id
        ("open_meteo", None, "resource_id"),
        # socrata_geojson with format != geojson
        ("socrata_geojson", "format", None),
    ],
)
def test_wrong_api_type_field_combination_rejected(isolated_config_dir, api_type, required, forbidden):
    """Cross-field validation: each api_type enforces its own field rules."""
    base_required = {
        "socrata": {"resource_id": "abc", "domain": "example.com"},
        "socrata_geojson": {"resource_id": "abc", "domain": "example.com", "format": "geojson"},
        "open_meteo": {"endpoint": "https://x"},
        "generic_rest": {"endpoint": "https://x"},
    }[api_type]

    fields = dict(base_required)
    if required:
        # "Missing required" branch: drop the field so the validator fires
        fields.pop(required, None)
    if forbidden:
        # We're testing the "extra forbidden" branch — add it
        fields[forbidden] = "https://wrong" if forbidden in {"endpoint"} else "wrong_value"

    ds_lines = "\n".join(f"    {k}: {v!r}" if isinstance(v, str) else f"    {k}: {v}" for k, v in fields.items())
    ds_lines = ds_lines.replace("'https://wrong'", '"https://wrong"').replace("'wrong_value'", '"wrong_value"')

    yaml_text = (
        "source:\n"
        "  id: SRC-TEST-001\n"
        "  name: combo\n"
        "  type: rest_api_socrata\n"
        "  owner: x\n"
        "  priority: P0\n"
        "  status: production\n"
        "datasets:\n"
        f"  - name: foo\n"
        f"    api_type: {api_type}\n"
        f"    timestamp_field: t\n"
        f"{ds_lines}\n"
    )
    (isolated_config_dir / "combo.yaml").write_text(yaml_text)

    with pytest.raises(ConfigLoadError):
        load_all_sources()


def test_config_dir_override_via_env_var(isolated_config_dir):
    """A single valid YAML in tmp_path is found via the env override."""
    (isolated_config_dir / "alt.yaml").write_text(
        "source:\n"
        "  id: SRC-ALT-001\n"
        "  name: alt\n"
        "  type: rest_api_socrata\n"
        "  owner: x\n"
        "  priority: P0\n"
        "  status: production\n"
        "datasets:\n"
        "  - name: foo\n"
        "    api_type: socrata\n"
        "    resource_id: abc\n"
        "    domain: example.com\n"
        "    timestamp_field: t\n"
    )
    cfg = load_source_config("SRC-ALT-001")
    assert cfg.source.id == "SRC-ALT-001"
    # And the canonical 4 sources are NOT visible when env is overridden
    with pytest.raises(ConfigLoadError):
        load_source_config("SRC-NYC-311")


def test_dataset_config_pydantic_validator_directly():
    """Direct test of DatasetConfig cross-field validation without YAML layer."""
    # Valid socrata
    DatasetConfig(
        name="ok",
        api_type=ApiType.SOCRATA,
        resource_id="r",
        domain="d",
        timestamp_field="t",
    )
    # Valid open_meteo
    DatasetConfig(
        name="ok",
        api_type=ApiType.OPEN_METEO,
        endpoint="https://x",
        timestamp_field="t",
    )
    # Invalid: socrata without resource_id
    with pytest.raises(ValidationError) as exc_info:
        DatasetConfig(
            name="bad",
            api_type=ApiType.SOCRATA,
            domain="d",
            timestamp_field="t",
        )
    assert "resource_id" in str(exc_info.value)
    # Invalid: open_meteo with socrata field
    with pytest.raises(ValidationError) as exc_info:
        DatasetConfig(
            name="bad",
            api_type=ApiType.OPEN_METEO,
            endpoint="https://x",
            resource_id="should-not-be-here",
            timestamp_field="t",
        )
    assert "resource_id" in str(exc_info.value)


def test_dataset_name_pattern_rejected_directly():
    """Dataset name must match ^[a-z0-9_]+$ — uppercase / hyphens / spaces all rejected."""
    for bad_name in ["Has-Space", "UPPER", "with-dash", ""]:
        with pytest.raises(ValidationError):
            DatasetConfig(
                name=bad_name,
                api_type=ApiType.OPEN_METEO,
                endpoint="https://x",
            )


# ── Loader direct behavior ───────────────────────────────────────────────────


def test_duplicate_source_id_in_two_files_raises(isolated_config_dir):
    (isolated_config_dir / "a.yaml").write_text(
        "source:\n  id: SRC-DUP-001\n  name: a\n  type: rest_api\n"
        "  owner: x\n  priority: P0\n  status: production\n"
        "datasets:\n  - name: x\n    api_type: open_meteo\n    endpoint: https://a\n"
    )
    (isolated_config_dir / "b.yaml").write_text(
        "source:\n  id: SRC-DUP-001\n  name: b\n  type: rest_api\n"
        "  owner: x\n  priority: P0\n  status: production\n"
        "datasets:\n  - name: x\n    api_type: open_meteo\n    endpoint: https://b\n"
    )
    with pytest.raises(ConfigLoadError) as exc_info:
        load_all_sources()
    assert "Duplicate" in str(exc_info.value)
    assert "SRC-DUP-001" in str(exc_info.value)


# ── snapshot partition strategy ──────────────────────────────────────────────


def test_snow_clearing_source_is_a_snapshot_with_no_timestamp_field():
    """The upstream has no time field at all — that is why snapshot exists."""
    cfg = load_source_config("SRC-WPG-SNOW")

    assert cfg.source.partition_strategy == "snapshot"
    assert cfg.datasets[0].timestamp_field is None
    assert cfg.datasets[0].resource_id == "g3p4-h83y"
    assert cfg.datasets[0].domain == "data.winnipeg.ca"


def test_snapshot_allows_a_null_timestamp_field(isolated_config_dir):
    """`daily` would reject this config; that rejection is why snapshot was added."""
    (isolated_config_dir / "snap.yaml").write_text(
        "source:\n"
        "  id: SRC-SNAP-001\n"
        "  name: snapshot source\n"
        "  type: rest_api_socrata\n"
        "  owner: city_operations\n"
        "  priority: P1\n"
        "  status: production\n"
        "  partition_strategy: snapshot\n"
        "datasets:\n"
        "  - name: ds1\n"
        "    api_type: socrata\n"
        "    resource_id: abcd-1234\n"
        "    domain: example.com\n"
        "    timestamp_field: null\n",
    )

    cfg = load_source_config("SRC-SNAP-001")

    assert cfg.source.partition_strategy == "snapshot"
    assert cfg.datasets[0].timestamp_field is None


def test_snapshot_also_allows_a_timestamp_field(isolated_config_dir):
    """It is optional, not forbidden: when present it fills the manifest date range."""
    (isolated_config_dir / "snap.yaml").write_text(
        "source:\n"
        "  id: SRC-SNAP-002\n"
        "  name: snapshot source\n"
        "  type: rest_api_socrata\n"
        "  owner: city_operations\n"
        "  priority: P1\n"
        "  status: production\n"
        "  partition_strategy: snapshot\n"
        "datasets:\n"
        "  - name: ds1\n"
        "    api_type: socrata\n"
        "    resource_id: abcd-1234\n"
        "    domain: example.com\n"
        "    timestamp_field: observed_at\n",
    )

    assert load_source_config("SRC-SNAP-002").datasets[0].timestamp_field == "observed_at"
