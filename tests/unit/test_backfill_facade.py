"""
Unit tests for ``BackfillFacade`` and ``BackfillError``.

What we cover:

- **Partition strategy dispatch** — the facade's ``_write_one`` calls
  ``loader.write_daily()`` for daily sources and
  ``loader.write_monthly_shard()`` for monthly sources. We mock the
  loader so the assertion is on which write method was called, not on
  object storage.

- **Per-dataset loader construction** — each dataset gets its own
  ``S3BronzeLoader`` with the correct ``timestamp_field``. This is
  load-bearing for NYPD, which has 4 datasets with different timestamp
  columns.

- **Shared S3 client** — when the facade is constructed with a
  ``client``, that single client is reused across all per-dataset
  loaders (avoids 4 storage clients for NYPD).

- **dataset_name filter** — uploading/fetching a single dataset from a
  multi-dataset source.

- **Partial failure** — when one of N datasets fails, the others
  continue; only when ALL fail does the facade raise
  :class:`BackfillError`.

- **BackfillError formatting** — the ``__str__`` includes source_id,
  dataset_name, and phase when set.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ingestion.backfill import BackfillError, BackfillFacade  # noqa: E402
from ingestion.config import (  # noqa: E402
    ApiType,
    DatasetConfig,
    SourceConfig,
    SourceMetadata,
)


def _fake_manifest(dataset_name: str, count: int = 1) -> SimpleNamespace:
    """Build a stand-in ManifestEntry that exposes the fields the facade logs.

    The real :class:`ManifestEntry` has 12 required fields; tests don't care
    about the ones the facade never touches.
    """
    return SimpleNamespace(
        record_count=count,
        filename=f"data_{dataset_name}.ndjson.gz",
        dataset_name=dataset_name,
    )


# ── Shared builders ──────────────────────────────────────────────────────────


def _facade(src: SourceConfig, **kwargs) -> BackfillFacade:
    """Build a facade with a stub S3 client.

    Unit tests must never construct a real boto3 client: it would demand the
    S3_* environment variables and turn a missing .env into 40 failing tests
    that say nothing about the facade.
    """
    kwargs.setdefault("client", MagicMock(name="s3_client"))
    return BackfillFacade(src, bucket="bkt", **kwargs)


def _mk_dataset(
    name: str,
    *,
    api_type: ApiType = ApiType.SOCRATA,
    timestamp_field: str | None = "t",
    resource_id: str = "abc",
    domain: str = "x.com",
    endpoint: str | None = None,
    partition_strategy: str | None = None,
) -> DatasetConfig:
    return DatasetConfig.model_construct(
        name=name, api_type=api_type, timestamp_field=timestamp_field,
        resource_id=resource_id, domain=domain,
        endpoint=endpoint, query_params=None, format=None,
        partition_strategy=partition_strategy,  # type: ignore[arg-type]
    )


def _mk_source(
    source_id: str = "SRC-FAKE-001",
    partition_strategy: str = "monthly",
    datasets: list[DatasetConfig] | None = None,
) -> SourceConfig:
    return SourceConfig.model_construct(
        source=SourceMetadata(
            id=source_id, name="fake", type="rest_api_socrata",  # type: ignore[arg-type]
            owner="x", priority="P2", status="production",
            partition_strategy=partition_strategy,  # type: ignore[arg-type]
            description=None,
        ),
        datasets=datasets or [_mk_dataset("ds1")],
    )


# ── _write_one: partition_strategy dispatch ──────────────────────────────────


def test_write_one_dispatches_to_write_daily_for_daily_source():
    """daily partition → records are split by date into per-day files."""
    src = _mk_source(partition_strategy="daily", datasets=[_mk_dataset("ds1")])
    facade = _facade(src)

    loader = facade._loaders["ds1"]
    loader.write_daily = MagicMock(return_value=[_fake_manifest("ds1")])  # type: ignore[method-assign]
    loader.write_monthly_shard = MagicMock()  # type: ignore[method-assign]

    result = facade._write_one(src.datasets[0], [{"x": 1}], date(2026, 6, 1))

    loader.write_daily.assert_called_once()
    loader.write_monthly_shard.assert_not_called()
    assert result == [_fake_manifest("ds1")]


def test_write_one_dispatches_to_write_monthly_shard_for_monthly_source():
    """monthly partition → one shard per month, filename uses start month."""
    src = _mk_source(partition_strategy="monthly", datasets=[_mk_dataset("ds1")])
    facade = _facade(src)

    loader = facade._loaders["ds1"]
    loader.write_daily = MagicMock()  # type: ignore[method-assign]
    loader.write_monthly_shard = MagicMock(return_value=_fake_manifest("ds1"))  # type: ignore[method-assign]

    result = facade._write_one(src.datasets[0], [{"x": 1}], date(2026, 6, 1))

    loader.write_monthly_shard.assert_called_once()
    loader.write_daily.assert_not_called()
    assert result == [_fake_manifest("ds1")]


def test_write_one_dispatches_to_write_snapshot_for_snapshot_source():
    """snapshot partition → one object under the collection date."""
    src = _mk_source(partition_strategy="snapshot", datasets=[_mk_dataset("ds1")])
    facade = _facade(src)

    loader = facade._loaders["ds1"]
    loader.write_snapshot = MagicMock(return_value=_fake_manifest("ds1"))  # type: ignore[method-assign]
    loader.write_daily = MagicMock()  # type: ignore[method-assign]
    loader.write_monthly_shard = MagicMock()  # type: ignore[method-assign]

    result = facade._write_one(
        src.datasets[0], [{"x": 1}], date(2026, 11, 3),
        ingest_date_override=date(2026, 11, 3),
    )

    loader.write_snapshot.assert_called_once()
    assert loader.write_snapshot.call_args.kwargs["ingest_date"] == date(2026, 11, 3)
    loader.write_daily.assert_not_called()
    loader.write_monthly_shard.assert_not_called()
    assert result == [_fake_manifest("ds1")]


# ── snapshot strategy guards ─────────────────────────────────────────────────


def test_upload_snapshot_requires_a_snapshot_source():
    facade = _facade(_mk_source(partition_strategy="daily"))

    with pytest.raises(ValueError, match="snapshot"):
        facade.upload_snapshot()


def test_upload_day_rejects_a_snapshot_source():
    """A snapshot source has no per-day history to request."""
    facade = _facade(_mk_source(partition_strategy="snapshot"))

    with pytest.raises(ValueError, match="daily"):
        facade.upload_day(date(2026, 11, 3))


def test_upload_snapshot_defaults_to_today():
    src = _mk_source(partition_strategy="snapshot", datasets=[_mk_dataset("ds1")])
    facade = _facade(src)
    facade._upload_window = MagicMock(return_value=[])  # type: ignore[method-assign]

    facade.upload_snapshot()

    assert facade._upload_window.call_args.kwargs["ingest_date_override"] == date.today()


def test_upload_snapshot_honours_an_explicit_ingest_date():
    src = _mk_source(partition_strategy="snapshot", datasets=[_mk_dataset("ds1")])
    facade = _facade(src)
    facade._upload_window = MagicMock(return_value=[])  # type: ignore[method-assign]

    facade.upload_snapshot(ingest_date=date(2026, 11, 3))

    assert facade._upload_window.call_args.kwargs["ingest_date_override"] == date(
        2026, 11, 3,
    )


# ── Per-dataset loader construction ──────────────────────────────────────────


def test_one_loader_per_dataset_with_correct_timestamp_field():
    """NYPD has 4 datasets with different timestamp_field columns;
    each must get its own loader with the right field."""
    src = _mk_source(
        source_id="SRC-NYPD-TEST",
        partition_strategy="monthly",
        datasets=[
            _mk_dataset("collisions", timestamp_field="crash_date"),
            _mk_dataset("complaints_historic", timestamp_field="cmplnt_fr_dt"),
            _mk_dataset("complaints_current", timestamp_field="cmplnt_fr_dt"),
            _mk_dataset("shooting", timestamp_field="occur_date"),
        ],
    )
    facade = _facade(src)

    assert set(facade._loaders) == {
        "collisions", "complaints_historic", "complaints_current", "shooting",
    }
    assert facade._loaders["collisions"].timestamp_field == "crash_date"
    assert facade._loaders["complaints_historic"].timestamp_field == "cmplnt_fr_dt"
    assert facade._loaders["complaints_current"].timestamp_field == "cmplnt_fr_dt"
    assert facade._loaders["shooting"].timestamp_field == "occur_date"


def test_loader_gets_empty_timestamp_field_when_dataset_has_none():
    src = _mk_source(datasets=[_mk_dataset("ds_static", timestamp_field=None)])
    facade = _facade(src)
    assert facade._loaders["ds_static"].timestamp_field == ""


# ── Shared S3 client ────────────────────────────────────────────────────────


def test_shared_s3_client_reused_across_per_dataset_loaders():
    shared_client = MagicMock(name="shared_client")
    src = _mk_source(
        datasets=[
            _mk_dataset("ds1"), _mk_dataset("ds2"), _mk_dataset("ds3"),
        ],
    )
    facade = _facade(src, client=shared_client)
    for loader in facade._loaders.values():
        assert loader._client is shared_client


# ── _resolve_datasets ───────────────────────────────────────────────────────


def test_resolve_datasets_returns_all_when_dataset_name_is_none():
    src = _mk_source(datasets=[_mk_dataset("a"), _mk_dataset("b"), _mk_dataset("c")])
    facade = _facade(src)
    assert [d.name for d in facade._resolve_datasets(None)] == ["a", "b", "c"]


def test_resolve_datasets_filters_to_single_dataset():
    src = _mk_source(datasets=[_mk_dataset("a"), _mk_dataset("b"), _mk_dataset("c")])
    facade = _facade(src)
    assert [d.name for d in facade._resolve_datasets("b")] == ["b"]


def test_resolve_datasets_raises_for_unknown_dataset():
    src = _mk_source(datasets=[_mk_dataset("a"), _mk_dataset("b")])
    facade = _facade(src)
    with pytest.raises(ValueError) as exc_info:
        facade._resolve_datasets("zzz")
    msg = str(exc_info.value)
    assert "zzz" in msg
    assert "SRC-FAKE-001" in msg
    assert "'a'" in msg and "'b'" in msg


# ── upload(): partial failure semantics ──────────────────────────────────────


def test_upload_continues_past_partial_dataset_failures():
    """3 datasets, the 2nd fails — the facade returns 2 manifests."""
    src = _mk_source(
        source_id="SRC-MULTI",
        partition_strategy="monthly",
        datasets=[
            _mk_dataset("ds_ok_1"),
            _mk_dataset("ds_fail"),
            _mk_dataset("ds_ok_2"),
        ],
    )
    facade = _facade(src)

    def fake_fetch(ds, start, end):
        if ds.name == "ds_fail":
            raise BackfillError("upstream timeout", source_id="SRC-MULTI",
                                dataset_name="ds_fail", phase="fetch")
        return [{"x": ds.name}]

    def fake_write(ds, records, start, month_partition_override=None, ingest_date_override=None):
        return [_fake_manifest(ds.name)]

    facade._fetch_one = fake_fetch  # type: ignore[method-assign]
    facade._write_one = fake_write  # type: ignore[method-assign]

    manifests = facade.upload_month(date(2026, 6, 1))
    assert manifests == [_fake_manifest("ds_ok_1"), _fake_manifest("ds_ok_2")]


def test_upload_skips_datasets_with_empty_records():
    src = _mk_source(
        source_id="SRC-MULTI",
        partition_strategy="monthly",
        datasets=[_mk_dataset("ds_empty"), _mk_dataset("ds_ok")],
    )
    facade = _facade(src)

    facade._fetch_one = lambda ds, start, end: [] if ds.name == "ds_empty" else [{"x": 1}]  # type: ignore[method-assign]
    facade._write_one = lambda ds, records, start, month_partition_override=None, ingest_date_override=None: [_fake_manifest(ds.name)]  # type: ignore[method-assign]

    manifests = facade.upload_month(date(2026, 6, 1))
    assert manifests == [_fake_manifest("ds_ok")]


def test_upload_raises_backfill_error_when_all_datasets_fail():
    src = _mk_source(
        source_id="SRC-ALLFAIL",
        partition_strategy="monthly",
        datasets=[_mk_dataset("a"), _mk_dataset("b")],
    )
    facade = _facade(src)

    def fake_fetch(ds, start, end):
        raise BackfillError("down", source_id="SRC-ALLFAIL",
                            dataset_name=ds.name, phase="fetch")

    facade._fetch_one = fake_fetch  # type: ignore[method-assign]

    with pytest.raises(BackfillError) as exc_info:
        facade.upload_month(date(2026, 6, 1))
    e = exc_info.value
    assert e.source_id == "SRC-ALLFAIL"
    assert e.phase == "upload"


def test_upload_wraps_unexpected_fetch_exception_in_backfill_error():
    """A non-BackfillError from the upstream client is caught by the real
    ``_fetch_one`` and wrapped in ``BackfillError`` with phase='fetch'."""
    src = _mk_source(datasets=[_mk_dataset("ds_crash")])
    facade = _facade(src)

    # Mock the upstream client to raise a raw RuntimeError (e.g. network gone).
    # The real SocrataFetcher propagates it; the real _fetch_one wraps it.
    with patch("ingestion.backfill.fetchers.socrata.SocrataClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.fetch_all_paginated.side_effect = RuntimeError("network gone")
        mock_client_cls.return_value = mock_client

        with pytest.raises(BackfillError) as exc_info:
            facade.upload_month(date(2026, 6, 1))
    e = exc_info.value
    # The outer "all-failed" BackfillError carries source_id + phase='upload'.
    assert e.source_id == "SRC-FAKE-001"
    assert e.phase == "upload"
    # The inner wrapped error is reachable via __cause__ — that's the
    # one with phase='fetch' and the dataset_name context we want to verify.
    cause = e.__cause__
    assert cause is not None
    assert cause.phase == "fetch"
    assert cause.dataset_name == "ds_crash"


def test_upload_wraps_write_exception_and_continues_to_next_dataset():
    """A write failure on one dataset does not stop the loop."""
    src = _mk_source(
        source_id="SRC-WRITE-FAIL",
        partition_strategy="monthly",
        datasets=[_mk_dataset("a"), _mk_dataset("b")],
    )
    facade = _facade(src)
    facade._fetch_one = lambda ds, start, end: [{"x": ds.name}]  # type: ignore[method-assign]

    def fake_write(ds, records, start, month_partition_override=None, ingest_date_override=None):
        if ds.name == "a":
            raise OSError("disk full")
        return [_fake_manifest(ds.name)]

    facade._write_one = fake_write  # type: ignore[method-assign]

    manifests = facade.upload_month(date(2026, 6, 1))
    assert manifests == [_fake_manifest("b")]


def test_upload_passes_dataset_name_filter_to_resolution():
    """The dataset_name filter should narrow the work to one dataset."""
    src = _mk_source(
        source_id="SRC-FILTER",
        partition_strategy="monthly",
        datasets=[_mk_dataset("alpha"), _mk_dataset("beta")],
    )
    facade = _facade(src)
    called_with: list[str] = []
    facade._fetch_one = lambda ds, start, end: called_with.append(ds.name) or [{"x": 1}]  # type: ignore[method-assign]
    facade._write_one = lambda ds, records, start, month_partition_override=None, ingest_date_override=None: [_fake_manifest(ds.name)]  # type: ignore[method-assign]

    manifests = facade.upload_month(
        date(2026, 6, 1), dataset_name="beta",
    )
    assert called_with == ["beta"]
    assert manifests == [_fake_manifest("beta")]


# ── fetch() semantics ───────────────────────────────────────────────────────


def test_fetch_returns_records_keyed_by_dataset_name():
    src = _mk_source(
        source_id="SRC-FETCH",
        partition_strategy="monthly",
        datasets=[_mk_dataset("alpha"), _mk_dataset("beta")],
    )
    facade = _facade(src)
    facade._fetch_one = lambda ds, start, end: [{"name": ds.name, "i": 1}]  # type: ignore[method-assign]

    out = facade.fetch_month(date(2026, 6, 1))
    assert set(out) == {"alpha", "beta"}
    assert out["alpha"] == [{"name": "alpha", "i": 1}]
    assert out["beta"] == [{"name": "beta", "i": 1}]


def test_fetch_skips_failing_datasets_keeps_successful_ones():
    src = _mk_source(
        source_id="SRC-PARTIAL-FETCH",
        partition_strategy="monthly",
        datasets=[_mk_dataset("ok"), _mk_dataset("fail"), _mk_dataset("also_ok")],
    )
    facade = _facade(src)

    def fake_fetch(ds, start, end):
        if ds.name == "fail":
            raise BackfillError("nope", source_id="SRC-PARTIAL-FETCH",
                                dataset_name="fail", phase="fetch")
        return [{"name": ds.name}]

    facade._fetch_one = fake_fetch  # type: ignore[method-assign]

    out = facade.fetch_month(date(2026, 6, 1))
    assert set(out) == {"ok", "also_ok"}


def test_fetch_raises_backfill_error_when_all_datasets_fail():
    src = _mk_source(
        source_id="SRC-ALL-FAIL-FETCH",
        partition_strategy="monthly",
        datasets=[_mk_dataset("a"), _mk_dataset("b")],
    )
    facade = _facade(src)

    def fake_fetch(ds, start, end):
        raise BackfillError("nope", source_id="SRC-ALL-FAIL-FETCH",
                            dataset_name=ds.name, phase="fetch")

    facade._fetch_one = fake_fetch  # type: ignore[method-assign]

    with pytest.raises(BackfillError) as exc_info:
        facade.fetch_month(date(2026, 6, 1))
    assert exc_info.value.phase == "fetch"


def test_fetch_unknown_dataset_raises_value_error():
    src = _mk_source(datasets=[_mk_dataset("only")])
    facade = _facade(src)
    with pytest.raises(ValueError, match="not_in_source"):
        facade.fetch_month(date(2026, 6, 1), dataset_name="not_in_source")


# ── BackfillError formatting ────────────────────────────────────────────────


def test_backfill_error_str_includes_all_context():
    e = BackfillError(
        "fetch failed",
        source_id="SRC-X", dataset_name="ds-x", phase="fetch",
    )
    s = str(e)
    assert "[fetch]" in s
    assert "source=SRC-X" in s
    assert "dataset=ds-x" in s
    assert "fetch failed" in s


def test_backfill_error_str_omits_unset_fields():
    e = BackfillError("oops")
    assert str(e) == "[error] oops"


def test_backfill_error_str_with_only_source_id():
    e = BackfillError("oops", source_id="SRC-X")
    s = str(e)
    assert "[error]" in s
    assert "source=SRC-X" in s
    assert "dataset=" not in s


# ── Cross-cutting: end-to-end upload via mocked SocrataFetcher ───────────────


def test_upload_full_path_with_socrata_fetcher_mocked():
    """End-to-end smoke: SocrataFetcher → BackfillFacade → write_daily mock."""
    src = _mk_source(
        source_id="SRC-NYC-311-TEST",
        partition_strategy="daily",
        datasets=[_mk_dataset("nyc_311", timestamp_field="created_date")],
    )

    fake_records = [
        {"unique_key": "1", "created_date": "2026-06-01T10:00:00.000"},
        {"unique_key": "2", "created_date": "2026-06-01T11:00:00.000"},
        {"unique_key": "3", "created_date": "2026-06-02T09:00:00.000"},
    ]
    with patch("ingestion.backfill.fetchers.socrata.SocrataClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.fetch_all_paginated.return_value = iter(fake_records)
        mock_client_cls.return_value = mock_client

        facade = _facade(src)
        # Replace write_daily on the loader to capture the call
        manifest_a = MagicMock(name="m_a")
        manifest_b = MagicMock(name="m_b")
        facade._loaders["nyc_311"].write_daily = MagicMock(  # type: ignore[method-assign]
            return_value=[manifest_a, manifest_b],
        )

        manifests = facade.upload_day(date(2026, 6, 1))
        assert manifests == [manifest_a, manifest_b]
        # Verify SocrataClient was called with the correct window
        kwargs = mock_client.fetch_all_paginated.call_args.kwargs
        assert kwargs["timestamp_field"] == "created_date"
        assert kwargs["start_dt"].date() == date(2026, 6, 1)
        assert kwargs["end_dt"].date() == date(2026, 6, 2)


# ── 6 atomic public methods: strategy-mismatch errors ────────────────────────


def test_upload_day_raises_on_monthly_source():
    """Calling upload_day on a monthly source is a caller bug → fail fast."""
    src = _mk_source(partition_strategy="monthly", datasets=[_mk_dataset("ds1")])
    facade = _facade(src)
    with pytest.raises(ValueError) as exc_info:
        facade.upload_day(date(2026, 6, 1))
    msg = str(exc_info.value)
    assert "upload_day" in msg or "daily" in msg
    assert "monthly" in msg


def test_upload_day_raises_on_static_source():
    src = _mk_source(partition_strategy="static", datasets=[_mk_dataset("ds1")])
    facade = _facade(src)
    with pytest.raises(ValueError) as exc_info:
        facade.upload_day(date(2026, 6, 1))
    assert "static" in str(exc_info.value)


def test_upload_month_raises_on_daily_source():
    src = _mk_source(partition_strategy="daily", datasets=[_mk_dataset("ds1")])
    facade = _facade(src)
    with pytest.raises(ValueError) as exc_info:
        facade.upload_month(date(2026, 6, 1))
    assert "daily" in str(exc_info.value)


def test_upload_static_raises_on_daily_source():
    src = _mk_source(partition_strategy="daily", datasets=[_mk_dataset("ds1")])
    facade = _facade(src)
    with pytest.raises(ValueError):
        facade.upload_static()


def test_upload_static_raises_on_monthly_source():
    src = _mk_source(partition_strategy="monthly", datasets=[_mk_dataset("ds1")])
    facade = _facade(src)
    with pytest.raises(ValueError):
        facade.upload_static()


def test_fetch_day_raises_on_wrong_strategy():
    src = _mk_source(partition_strategy="monthly", datasets=[_mk_dataset("ds1")])
    facade = _facade(src)
    with pytest.raises(ValueError):
        facade.fetch_day(date(2026, 6, 1))


def test_fetch_month_raises_on_wrong_strategy():
    src = _mk_source(partition_strategy="daily", datasets=[_mk_dataset("ds1")])
    facade = _facade(src)
    with pytest.raises(ValueError):
        facade.fetch_month(date(2026, 6, 1))


def test_fetch_static_raises_on_wrong_strategy():
    src = _mk_source(partition_strategy="monthly", datasets=[_mk_dataset("ds1")])
    facade = _facade(src)
    with pytest.raises(ValueError):
        facade.fetch_static()


# ── upload_month: month param validation ─────────────────────────────────────


def test_upload_month_rejects_non_first_of_month():
    """upload_month expects date(YYYY, MM, 1); reject mid-month dates."""
    src = _mk_source(partition_strategy="monthly", datasets=[_mk_dataset("ds1")])
    facade = _facade(src)
    with pytest.raises(ValueError, match="first day"):
        facade.upload_month(date(2026, 6, 15))


# ── upload_static: month_partition_override flows through to loader ──────────


def test_upload_static_passes_static_label_to_monthly_shard():
    """upload_static() forces the shard name to 'static' (not today's month)."""
    src = _mk_source(partition_strategy="static", datasets=[_mk_dataset("ds1")])
    facade = _facade(src)
    facade._fetch_one = lambda ds, start, end: [{"x": 1}]  # type: ignore[method-assign]
    loader = facade._loaders["ds1"]
    # The real write_monthly_shard returns a single ManifestEntry (not a list);
    # _write_one wraps it in a list.
    loader.write_monthly_shard = MagicMock(  # type: ignore[method-assign]
        return_value=_fake_manifest("ds1"),
    )

    manifests = facade.upload_static()

    # The loader's write_monthly_shard was called with month_partition='static'.
    loader.write_monthly_shard.assert_called_once()
    kwargs = loader.write_monthly_shard.call_args.kwargs
    assert kwargs["month_partition"] == "static"
    # _write_one wraps the single ManifestEntry returned by the loader
    # in a list (one per day for daily, one per dataset for monthly).
    assert manifests == [_fake_manifest("ds1")]


# ── fetch_static: same contract, no write ────────────────────────────────────


def test_fetch_static_works_on_static_source():
    src = _mk_source(partition_strategy="static", datasets=[_mk_dataset("ds1")])
    facade = _facade(src)
    facade._fetch_one = lambda ds, start, end: [{"x": 1}]  # type: ignore[method-assign]

    out = facade.fetch_static()
    assert out == {"ds1": [{"x": 1}]}


# ── upload_window / fetch_window: wide-fetch API entry points ───────────────


def test_upload_window_dispatches_to_window_method():
    """upload_window should call _upload_window once with the given window."""
    src = _mk_source(partition_strategy="daily", datasets=[_mk_dataset("ds1")])
    facade = _facade(src)
    captured: dict = {}
    def fake_upload(start, end, dataset_name, strategy=None):
        captured["start"] = start
        captured["end"] = end
        captured["dataset_name"] = dataset_name
        captured["strategy"] = strategy
        return [SimpleNamespace(record_count=5, filename="x.json", dataset_name="ds1")]
    facade._upload_window = fake_upload  # type: ignore[method-assign]

    manifests = facade.upload_window(date(2026, 6, 1), date(2026, 6, 8), "alpha")
    assert captured == {
        "start": date(2026, 6, 1), "end": date(2026, 6, 8),
        "dataset_name": "alpha", "strategy": None,
    }
    assert len(manifests) == 1


def test_upload_window_raises_on_static_source():
    """upload_window does not fit static — direct callers to upload_static."""
    src = _mk_source(partition_strategy="static", datasets=[_mk_dataset("ds1")])
    facade = _facade(src)
    with pytest.raises(ValueError) as exc_info:
        facade.upload_window(date(2026, 6, 1), date(2026, 6, 8))
    msg = str(exc_info.value)
    assert "static" in msg
    assert "upload_static" in msg


def test_upload_window_raises_on_monthly_source():
    """Monthly sources should use upload_month, not upload_window."""
    src = _mk_source(partition_strategy="monthly", datasets=[_mk_dataset("ds1")])
    facade = _facade(src)
    # Monthly still works via upload_window — only static is rejected.
    # (For monthly, the call would write a single shard at the start month.)
    facade._upload_window = MagicMock(return_value=[])  # type: ignore[method-assign]
    manifests = facade.upload_window(date(2026, 6, 1), date(2026, 7, 1))
    assert manifests == []


def test_fetch_window_calls_fetch_window_with_window():
    src = _mk_source(partition_strategy="daily", datasets=[_mk_dataset("ds1")])
    facade = _facade(src)
    facade._fetch_window = MagicMock(return_value={"ds1": [{"x": 1}]})  # type: ignore[method-assign]

    out = facade.fetch_window(date(2026, 6, 1), date(2026, 6, 8), "alpha")
    facade._fetch_window.assert_called_once_with(
        date(2026, 6, 1), date(2026, 6, 8), "alpha", strategy=None,
    )
    assert out == {"ds1": [{"x": 1}]}


def test_fetch_window_raises_on_static_source():
    src = _mk_source(partition_strategy="static", datasets=[_mk_dataset("ds1")])
    facade = _facade(src)
    with pytest.raises(ValueError, match="fetch_static"):
        facade.fetch_window(date(2026, 6, 1), date(2026, 6, 8))


# ── A snapshot dataset must never be written from a windowed call ────────────
#
# A source may mix strategies (the weather source's archive is `daily`, its
# forecast is `snapshot`). Every windowed entry point therefore has to state
# which datasets it means, and the facade has to refuse the combination
# outright rather than trust it: a snapshot upstream keeps no history, so
# writing "whatever it holds right now" into a partition named after some
# other window's start overwrites — or invents — that collection day, on the
# only copy that will ever exist, without raising anywhere.


def _mixed_source() -> SourceConfig:
    """A source whose datasets resolve to different effective strategies."""
    return _mk_source(
        partition_strategy="daily",
        datasets=[
            _mk_dataset("archive"),
            _mk_dataset("forecast", partition_strategy="snapshot"),
        ],
    )


def test_upload_window_without_strategy_refuses_to_write_a_snapshot_dataset():
    facade = _facade(_mixed_source())
    facade._fetch_one = lambda ds, start, end: [{"x": 1}]  # type: ignore[method-assign]

    with pytest.raises(BackfillError) as exc_info:
        facade.upload_window(date(2026, 6, 1), date(2026, 6, 8))

    msg = str(exc_info.value)
    assert "forecast" in msg
    assert "snapshot" in msg


def test_upload_window_with_daily_strategy_writes_only_the_daily_dataset():
    src = _mixed_source()
    facade = _facade(src)
    facade._fetch_one = lambda ds, start, end: [{"x": 1}]  # type: ignore[method-assign]
    written: list[str] = []
    facade._write_one = (  # type: ignore[method-assign]
        lambda ds, records, start, **kw: written.append(ds.name) or [_fake_manifest(ds.name)]
    )

    facade.upload_window(date(2026, 6, 1), date(2026, 6, 8), strategy="daily")
    assert written == ["archive"]


def test_upload_snapshot_still_writes_the_snapshot_dataset():
    """The guard keys on ingest_date_override, so the legitimate path is unaffected."""
    src = _mixed_source()
    facade = _facade(src)
    facade._fetch_one = lambda ds, start, end: [{"x": 1}]  # type: ignore[method-assign]
    written: list[str] = []
    facade._write_one = (  # type: ignore[method-assign]
        lambda ds, records, start, **kw: written.append(ds.name) or [_fake_manifest(ds.name)]
    )

    facade.upload_snapshot(ingest_date=date(2026, 6, 1))
    assert written == ["forecast"]


# ── _fetch_one: the effective strategy reaches the fetcher factory ───────────
#
# Every other test in this file mocks `_fetch_one`, so the one line inside it
# that picks a fetcher was covered by nothing. That line is exactly where the
# config layer and the fetch layer can disagree: `static` forbids a
# `timestamp_field`, and a Socrata fetcher built without knowing the strategy
# demands one — so a static Socrata source could not be ingested at all.


def test_fetch_one_passes_the_datasets_effective_strategy_to_the_factory():
    src = _mk_source(
        partition_strategy="static",
        datasets=[_mk_dataset("ds1", timestamp_field=None)],
    )
    facade = _facade(src)

    with patch("ingestion.backfill.facade.build_fetcher") as mock_build:
        mock_build.return_value = SimpleNamespace(fetch=lambda: iter([{"x": 1}]))
        records = facade._fetch_one(src.datasets[0], date(2026, 6, 1), date(2026, 6, 8))

    assert records == [{"x": 1}]
    assert mock_build.call_args.kwargs["strategy"] == "static"


def test_fetch_one_honours_a_per_dataset_strategy_override():
    """A dataset override wins over the source's strategy, here too.

    The facade resolves the strategy through `strategy_for`, so a source whose
    datasets are partitioned differently (a daily archive next to a snapshot
    forecast) sends each dataset to the factory under its own strategy rather
    than the source-level one.
    """
    ds = _mk_dataset("ds_snap", timestamp_field=None)
    ds.partition_strategy = "snapshot"
    src = _mk_source(partition_strategy="daily", datasets=[ds])
    facade = _facade(src)

    with patch("ingestion.backfill.facade.build_fetcher") as mock_build:
        mock_build.return_value = SimpleNamespace(fetch=lambda: iter([]))
        facade._fetch_one(ds, date(2026, 6, 1), date(2026, 6, 8))

    assert mock_build.call_args.kwargs["strategy"] == "snapshot"
