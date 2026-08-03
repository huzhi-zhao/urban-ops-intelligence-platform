"""
Unit tests for the per-source backfill scripts and the shared CLI helpers.

Covers:

- ``scripts.backfill._common`` — ``parse_date``, ``parse_args``,
  ``require_bucket``, ``default_max_workers``, dispatch tables.
- The four ``backfill_*.py`` per-source scripts — each one's ``run()``
  function delegates to the right ``scripts.backfill.bulk`` function:

  * ``--action upload``  → bulk's backfill_*_window(static: backfill_static)
  * ``--action fetch``   → bulk's fetch_*_window  (dry-run equivalent)
  * ``--dry-run``        → same as ``--action fetch``
  * Any chunk failed     → ``SystemExit(2)``
  * missing bucket       → ``SystemExit(1)``

Each per-source script is exercised via a uniform parametrized table —
adding a fifth source just means adding a row.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ingestion.config import load_source_config  # noqa: E402

# ── Per-source-script table ──────────────────────────────────────────────────
#
# Discovered, not enumerated. Every per-source script has the same shape
# (SOURCE_ID + a run() that dispatches to bulk by partition strategy), so the
# table is built by the same pkgutil scan main.py uses. Dropping in a new
# backfill_<slug>.py therefore extends this suite automatically, and retiring a
# city's scripts does not leave a stale literal behind for someone to delete.


def _discover_script_modules():
    import importlib
    import pkgutil

    import scripts.backfill as pkg

    params = []
    for mod_info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if not mod_info.name.startswith("backfill_"):
            continue
        module = importlib.import_module(f"scripts.backfill.{mod_info.name}")
        params.append(
            pytest.param(
                module,
                module.SOURCE_ID,
                id=mod_info.name.removeprefix("backfill_"),
            ),
        )
    return params


SCRIPT_TABLE = _discover_script_modules()


def _strategy_of(source_id: str) -> str:
    """Partition strategy for a source, read from its registry YAML."""
    return load_source_config(source_id).source.partition_strategy


# Strategies whose bulk call takes a [start, end) window and a worker count.
# `static` and `snapshot` are single indivisible documents: they take neither.
WINDOWED_STRATEGIES = {"daily", "monthly"}


# ── _common helpers ──────────────────────────────────────────────────────────


def test_parse_date_accepts_iso_format():
    from scripts.backfill._common import parse_date
    assert parse_date("2026-06-13") == date(2026, 6, 13)


def test_parse_date_rejects_other_formats():
    from scripts.backfill._common import parse_date
    for bad in ["2026/06/13", "06-13-2026", "13-06-2026", "not-a-date", "", "2026-13-01"]:
        with pytest.raises(ValueError):
            parse_date(bad)


def test_require_bucket_uses_flag_value(monkeypatch):
    from scripts.backfill._common import require_bucket
    monkeypatch.setenv("S3_BUCKET_NAME", "env-bucket")
    args = argparse.Namespace(bucket="flag-bucket")
    assert require_bucket(args) == "flag-bucket"


def test_require_bucket_falls_back_to_env(monkeypatch):
    from scripts.backfill._common import require_bucket
    monkeypatch.setenv("S3_BUCKET_NAME", "env-bucket")
    args = argparse.Namespace(bucket=None)
    assert require_bucket(args) == "env-bucket"


def test_require_bucket_exits_1_when_missing(monkeypatch):
    from scripts.backfill._common import require_bucket
    monkeypatch.delenv("S3_BUCKET_NAME", raising=False)
    args = argparse.Namespace(bucket=None)
    with pytest.raises(SystemExit) as exc_info:
        require_bucket(args)
    assert exc_info.value.code == 1


def test_require_bucket_exits_1_when_both_empty(monkeypatch):
    from scripts.backfill._common import require_bucket
    monkeypatch.setenv("S3_BUCKET_NAME", "")
    args = argparse.Namespace(bucket="")
    with pytest.raises(SystemExit) as exc_info:
        require_bucket(args)
    assert exc_info.value.code == 1


def test_parse_args_requires_start_and_end():
    from scripts.backfill._common import parse_args
    with pytest.raises(SystemExit):
        parse_args("desc")
    with pytest.raises(SystemExit):
        # sys.argv: only --end, missing --start
        with patch("sys.argv", ["script", "--end", "2026-06-13"]):
            parse_args("desc")


def test_parse_args_defaults_action_to_upload():
    from scripts.backfill._common import parse_args
    with patch("sys.argv", [
        "script",
        "--start", "2026-06-01",
        "--end", "2026-06-08",
    ]):
        args = parse_args("desc")
    assert args.start == date(2026, 6, 1)
    assert args.end == date(2026, 6, 8)
    assert args.action == "upload"
    assert args.bucket is None
    assert args.dry_run is False
    assert args.max_workers is None


def test_parse_args_accepts_dry_run_and_max_workers():
    from scripts.backfill._common import parse_args
    with patch("sys.argv", [
        "script",
        "--start", "2026-06-01",
        "--end", "2026-06-08",
        "--dry-run",
        "--max-workers", "2",
    ]):
        args = parse_args("desc")
    assert args.dry_run is True
    assert args.max_workers == 2


def test_default_max_workers_per_strategy():
    from scripts.backfill._common import default_max_workers
    assert default_max_workers("daily") == 4
    assert default_max_workers("monthly") == 2
    assert default_max_workers("static") == 1
    # snapshot is a single indivisible document — parallelism has nothing to split
    assert default_max_workers("snapshot") == 1
    # Unknown strategy → daily default
    assert default_max_workers("nonsense") == 4


# ── Per-source script: upload path ───────────────────────────────────────────


def _build_args(source_id, action="upload", dataset=None,
                bucket="test-bucket", dry_run=False, max_workers=4):
    """Universal args Namespace for --start / --end scripts."""
    return argparse.Namespace(
        start=date(2026, 6, 1),
        end=date(2026, 6, 8),
        action=action,
        bucket=bucket,
        dataset=dataset,
        dry_run=dry_run,
        max_workers=max_workers,
    )


@pytest.mark.parametrize("script_module, source_id", SCRIPT_TABLE)
def test_per_source_script_upload_calls_bulk_upload_window(
    script_module, source_id, monkeypatch
):
    """``--action upload`` calls the right bulk upload function.

    The dispatch table ``UPLOAD_DISPATCH[strategy]`` stores the function
    object captured at import time, so we swap the value in the dict
    itself (via ``patch.dict``) rather than patching the symbol in
    ``bulk`` (which would not affect the captured reference).
    """
    import scripts.backfill._common as common
    strategy = _strategy_of(source_id)
    fake_fn = MagicMock(return_value=[
        SimpleNamespace(document=date(2026, 6, 1), status="ok",
                        manifest_count=10, error=None),
    ])

    with patch.dict(common.UPLOAD_DISPATCH, {strategy: fake_fn}, clear=False):
        args = _build_args(source_id)
        script_module.run(args)

    fake_fn.assert_called_once()
    call = fake_fn.call_args
    # source_id is the first positional arg (matches the bulk function signature)
    assert call.args[0] == source_id
    # bucket is always present (kicked up by require_bucket)
    assert call.kwargs.get("bucket") == "test-bucket"
    # start/end/max_workers are only passed for daily/monthly — a static or
    # snapshot document has no window to slice.
    if strategy in WINDOWED_STRATEGIES:
        assert call.kwargs["start"] == date(2026, 6, 1)
        assert call.kwargs["end"] == date(2026, 6, 8)
        assert call.kwargs.get("max_workers") == 4


# ── Per-source script: fetch path ───────────────────────────────────────────


@pytest.mark.parametrize("script_module, source_id", SCRIPT_TABLE)
def test_per_source_script_fetch_calls_bulk_fetch_window(
    script_module, source_id, monkeypatch
):
    """``--action fetch`` calls the right bulk fetch function (no object-storage write)."""
    import scripts.backfill._common as common
    strategy = _strategy_of(source_id)
    fake_fn = MagicMock(return_value=[])

    with patch.dict(common.FETCH_DISPATCH, {strategy: fake_fn}, clear=False):
        args = _build_args(source_id, action="fetch", bucket=None)
        script_module.run(args)

    fake_fn.assert_called_once()
    call = fake_fn.call_args
    assert call.args[0] == source_id


@pytest.mark.parametrize("script_module, source_id", SCRIPT_TABLE)
def test_per_source_script_dry_run_calls_bulk_fetch(
    script_module, source_id, monkeypatch
):
    """``--dry-run`` calls the bulk fetch function, not upload."""
    import scripts.backfill._common as common
    strategy = _strategy_of(source_id)
    fake_fetch = MagicMock(return_value=[])
    fake_upload = MagicMock(return_value=[])

    with patch.dict(common.FETCH_DISPATCH, {strategy: fake_fetch}, clear=False), \
         patch.dict(common.UPLOAD_DISPATCH, {strategy: fake_upload}, clear=False):
        args = _build_args(source_id, action="upload", dry_run=True)
        script_module.run(args)

    fake_fetch.assert_called_once()
    fake_upload.assert_not_called()


# ── Per-source script: exit codes ────────────────────────────────────────────


@pytest.mark.parametrize("script_module, source_id", SCRIPT_TABLE)
def test_per_source_script_any_chunk_failed_exits_2(
    script_module, source_id, monkeypatch
):
    """If bulk returns any failed result, the script exits 2."""
    import scripts.backfill._common as common
    strategy = _strategy_of(source_id)
    fake_results = [
        SimpleNamespace(document=date(2026, 6, 1), status="ok",
                        manifest_count=10, error=None),
        SimpleNamespace(document=date(2026, 6, 2), status="failed",
                        manifest_count=0, error="boom"),
    ]
    fake_fn = MagicMock(return_value=fake_results)
    with patch.dict(common.UPLOAD_DISPATCH, {strategy: fake_fn}, clear=False):
        args = _build_args(source_id)
        with pytest.raises(SystemExit) as exc_info:
            script_module.run(args)
    assert exc_info.value.code == 2


@pytest.mark.parametrize("script_module, source_id", SCRIPT_TABLE)
def test_per_source_script_all_ok_does_not_exit(
    script_module, source_id, monkeypatch
):
    """All-OK bulk results → script returns normally (no SystemExit)."""
    import scripts.backfill._common as common
    strategy = _strategy_of(source_id)
    fake_results = [
        SimpleNamespace(document=date(2026, 6, 1), status="ok",
                        manifest_count=10, error=None),
    ]
    fake_fn = MagicMock(return_value=fake_results)
    with patch.dict(common.UPLOAD_DISPATCH, {strategy: fake_fn}, clear=False):
        args = _build_args(source_id)
        # Should NOT raise.
        script_module.run(args)


@pytest.mark.parametrize("script_module, source_id", SCRIPT_TABLE)
def test_per_source_script_missing_bucket_exits_1(
    script_module, source_id, monkeypatch
):
    """Upload without ``--bucket`` and no env var → ``SystemExit(1)``."""
    monkeypatch.delenv("S3_BUCKET_NAME", raising=False)
    args = _build_args(source_id, bucket=None)
    # No bulk mocking needed — require_bucket exits before bulk is called.
    with pytest.raises(SystemExit) as exc_info:
        script_module.run(args)
    assert exc_info.value.code == 1


# ── Per-source script: source id is correct ──────────────────────────────────


@pytest.mark.parametrize("script_module, source_id", SCRIPT_TABLE)
def test_per_source_script_loads_correct_source_id(
    script_module, source_id, monkeypatch
):
    """Each script pins the right ``SOURCE_ID`` constant."""
    assert script_module.SOURCE_ID == source_id


# ── Per-source script: signature sanity ───────────────────────────────────────


@pytest.mark.parametrize("script_module, source_id", SCRIPT_TABLE)
def test_per_source_script_run_signature(script_module, source_id):
    """The script's run() function should accept an argparse.Namespace."""
    import inspect
    sig = inspect.signature(script_module.run)
    assert len(sig.parameters) == 1
    assert "args" in sig.parameters
