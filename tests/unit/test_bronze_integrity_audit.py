"""Unit tests for the Bronze content integrity audit (checks B + C).

These cover the orchestration only — target derivation, windowing, the
report/re-pull list, and the rules that the postmortem calls load-bearing. The
measuring itself belongs to the two probes and has its own tests; mocking them
here keeps the suite offline (no object storage, no upstream, no Spark).

The rules under test come from
docs/dev/postmortem/bronze-socrata-pagination-incident.md 「落地时必须遵守的四条」.
Each one broke something real once, which is why it is asserted rather than
assumed.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from scripts.profiling.bronze_integrity_audit import (
    DatasetAudit,
    _window_days,
    audit_dataset,
    format_report,
    integrity_targets,
)

# ── Windowing ─────────────────────────────────────────────────────────────────

def test_window_excludes_today_and_covers_exactly_n_days():
    days = _window_days(date(2026, 8, 20), 14)

    assert len(days) == 14
    # Today's ingest has not necessarily run; auditing it reports a gap every
    # morning that closes itself by lunchtime.
    assert "2026-08-20" not in days
    assert "2026-08-19" in days
    assert "2026-08-06" in days
    assert "2026-08-05" not in days


def test_window_crosses_a_month_boundary():
    days = _window_days(date(2026, 9, 3), 5)

    assert days == {
        "2026-09-02", "2026-09-01", "2026-08-31", "2026-08-30", "2026-08-29",
    }


# ── Target derivation (postmortem rule 2: snapshot is never reconciled) ───────

def test_targets_come_from_the_registry_not_a_hardcoded_list():
    targets = integrity_targets()

    # Discovery-style assertion: the point is that the derivation runs, not that
    # any particular city is deployed.
    assert all(isinstance(sid, str) and isinstance(ds, str) for sid, ds in targets)
    assert len(targets) == len(set(targets)), "a dataset must not be audited twice"


def test_snapshot_and_static_datasets_are_never_targets():
    from ingestion.config import load_all_sources

    targets = set(integrity_targets())
    for cfg in load_all_sources().values():
        for ds in cfg.datasets:
            strategy = cfg.strategy_for(ds)
            if strategy in ("snapshot", "static", "monthly"):
                assert (cfg.source.id, ds.name) not in targets, (
                    f"{cfg.source.id}/{ds.name} is {strategy}: checks B/C are "
                    "day-shaped, and a snapshot upstream keeps no history to "
                    "reconcile against"
                )


def test_non_socrata_daily_datasets_are_excluded():
    """Check C needs a ``$select=count(*)`` endpoint; Open-Meteo has none.

    Including one would make the audit fail rather than skip — an error that
    looks like a data problem but is a targeting problem.
    """
    from ingestion.config import ApiType, load_all_sources

    targets = set(integrity_targets())
    for cfg in load_all_sources().values():
        for ds in cfg.datasets:
            if cfg.strategy_for(ds) == "daily" and ds.api_type != ApiType.SOCRATA:
                assert (cfg.source.id, ds.name) not in targets


# ── Check B skips rather than guessing (postmortem rule 1) ────────────────────

class _FakeDataset:
    def __init__(self, primary_key):
        self.primary_key = primary_key


def _no_findings(*_args, **_kwargs):
    return []


def _empty_reconcile_summary(_results):
    return {
        "days_checked": 0, "days_clean": 0, "days_with_missing_rows": 0,
        "days_with_excess_rows": 0, "rows_missing_total": 0,
        "rows_excess_total": 0, "days_tolerated_as_late_arrival": 0,
        "refetch_days": [], "mismatches": [],
    }


def test_check_b_is_skipped_when_no_primary_key_is_declared():
    """No key means skip, never guess.

    A guessed key is worse than no check: `case_id` alone looks like a
    plausible key for 311 and is 1.87% duplicated upstream, so guessing it
    would report a fifth of Bronze as corrupt.
    """
    with (
        patch("scripts.profiling.bronze_integrity_audit._dataset_config",
              return_value=_FakeDataset(None)),
        patch("scripts.profiling.bronze_duplicate_scan.run_scan") as scan,
        patch("scripts.profiling.bronze_rowcount_reconcile.run_reconcile",
              side_effect=_no_findings),
        patch("scripts.profiling.bronze_rowcount_reconcile.summarize",
              side_effect=_empty_reconcile_summary),
    ):
        result = audit_dataset("uoip", "SRC-X", "ds", only_days={"2026-08-19"})

    scan.assert_not_called()
    assert "B" not in result.checks_run
    assert "B" in result.checks_skipped
    assert result.checks_run == ["C"], "C must still run when B is skipped"


def test_a_declared_key_is_passed_through_to_the_scan():
    with (
        patch("scripts.profiling.bronze_integrity_audit._dataset_config",
              return_value=_FakeDataset(["case_id", "interaction_id"])),
        patch("scripts.profiling.bronze_duplicate_scan.run_scan",
              side_effect=_no_findings) as scan,
        patch("scripts.profiling.bronze_duplicate_scan.summarize",
              return_value={"shards_scanned": 3, "shards_multipage": 2,
                            "shards_dirty": 0, "excess_rows_total": 0,
                            "dirty_shards": []}),
        patch("scripts.profiling.bronze_rowcount_reconcile.run_reconcile",
              side_effect=_no_findings),
        patch("scripts.profiling.bronze_rowcount_reconcile.summarize",
              side_effect=_empty_reconcile_summary),
    ):
        result = audit_dataset("uoip", "SRC-X", "ds", only_days={"2026-08-19"})

    assert scan.call_args.kwargs["key_fields"] == ("case_id", "interaction_id")
    assert result.checks_run == ["B", "C"]
    assert result.is_clean


# ── Neither check may mask the other ──────────────────────────────────────────

def test_check_c_still_runs_when_check_b_raises():
    """The run that most needs both checks is one where something is wrong.

    B and C find different failures — a page-boundary slip repeats one row and
    drops another, and the two cancel out in the row count. Letting a broken B
    skip C would leave the drop invisible, which is the whole reason C exists.
    """
    with (
        patch("scripts.profiling.bronze_integrity_audit._dataset_config",
              return_value=_FakeDataset(["case_id"])),
        patch("scripts.profiling.bronze_duplicate_scan.run_scan",
              side_effect=OSError("object storage unreachable")),
        patch("scripts.profiling.bronze_rowcount_reconcile.run_reconcile",
              side_effect=_no_findings) as reconcile,
        patch("scripts.profiling.bronze_rowcount_reconcile.summarize",
              side_effect=_empty_reconcile_summary),
    ):
        result = audit_dataset("uoip", "SRC-X", "ds", only_days=None)

    reconcile.assert_called_once()
    assert result.checks_run == ["C"]
    assert any("check B raised" in e for e in result.errors)
    assert not result.is_clean, "an audit that could not run is not a clean audit"


# ── The re-pull list ──────────────────────────────────────────────────────────

def test_refetch_list_covers_both_directions_and_deduplicates():
    """Excess and missing are two halves of one defect, so both get re-pulled.

    The same day can surface in check B (a repeat) and check C (the matching
    drop). It must appear once in the re-pull list, not twice.
    """
    audit = DatasetAudit(
        source_id="SRC-X", dataset="ds",
        dirty_shards=[
            "bronze/raw/SRC-X/ds/2019-06/data_2019-06-07.ndjson.gz",
            "bronze/raw/SRC-X/ds/2025-09/data_2025-09-14.ndjson.gz",
        ],
        days_missing_rows=["2019-06-07", "2021-01-07"],
        days_excess_rows=["2025-09-14"],
    )

    assert audit.refetch_days == [
        "2019-06-07", "2021-01-07", "2025-09-14",
    ]
    assert not audit.is_clean


def test_a_shard_key_without_a_date_does_not_poison_the_list():
    audit = DatasetAudit(
        source_id="SRC-X", dataset="ds",
        dirty_shards=["bronze/raw/SRC-X/ds/data_static.ndjson.gz"],
    )

    assert audit.refetch_days == []


def test_a_clean_audit_has_no_refetch_days():
    audit = DatasetAudit(source_id="SRC-X", dataset="ds", checks_run=["B", "C"])

    assert audit.is_clean
    assert audit.refetch_days == []


# ── Report formatting ─────────────────────────────────────────────────────────

def test_report_names_the_days_to_re_pull():
    report = format_report([
        DatasetAudit(source_id="SRC-A", dataset="ok", checks_run=["B", "C"]),
        DatasetAudit(
            source_id="SRC-B", dataset="bad", checks_run=["B", "C"],
            days_missing_rows=["2019-06-07"],
        ),
    ])

    assert "OK   SRC-A/ok" in report
    assert "FAIL SRC-B/bad" in report
    assert "2019-06-07" in report


def test_report_states_why_check_b_was_skipped():
    report = format_report([
        DatasetAudit(
            source_id="SRC-A", dataset="nokey", checks_run=["C"],
            checks_skipped={"B": "no primary_key declared"},
        ),
    ])

    # A silently-skipped check is indistinguishable from a passing one.
    assert "B skipped" in report


def test_report_handles_no_eligible_datasets():
    assert "no datasets" in format_report([])


# ── The config field the audit reads ──────────────────────────────────────────

def test_declared_primary_key_must_be_usable():
    """Empty is rejected; unset is the way to say "no key".

    They are not the same thing: unset skips check B, while an empty key list
    would make every row share one key and report the whole shard duplicated.
    """
    from pydantic import ValidationError

    from ingestion.config import DatasetConfig

    base = {"name": "ds", "api_type": "socrata", "resource_id": "a-b", "domain": "x.example"}

    assert DatasetConfig(**base).primary_key is None
    assert DatasetConfig(**base, primary_key=["a", "b"]).primary_key == ["a", "b"]

    with pytest.raises(ValidationError, match="must not be empty"):
        DatasetConfig(**base, primary_key=[])
    with pytest.raises(ValidationError, match="repeated fields"):
        DatasetConfig(**base, primary_key=["a", "a"])
    with pytest.raises(ValidationError, match="blank field name"):
        DatasetConfig(**base, primary_key=["a", "  "])


# ── The DAG's Param default must not drift from the module's ─────────────────

def test_param_default_mirrors_the_audit_module():
    """The DAG duplicates this constant; the duplication must stay honest.

    It is duplicated on purpose — a Param default is needed at DAG parse time,
    and ``dag_audit_bronze`` imports everything else at task run time so that a
    broken module fails one task instead of stopping the scheduler from parsing
    the DAG. This test is the price of that choice.

    Read as text rather than imported so the offline suite covers it: importing
    the DAG needs apache-airflow, which is only installed in ``.venv-airflow``.
    """
    import re
    from pathlib import Path

    from scripts.profiling.bronze_integrity_audit import DEFAULT_LATE_ARRIVAL_DAYS

    dag_src = (
        Path(__file__).resolve().parents[2] / "dags" / "dag_audit_bronze.py"
    ).read_text()
    match = re.search(r"^DEFAULT_LATE_ARRIVAL_DAYS = (\d+)$", dag_src, re.MULTILINE)

    assert match, "dag_audit_bronze must define DEFAULT_LATE_ARRIVAL_DAYS"
    assert int(match.group(1)) == DEFAULT_LATE_ARRIVAL_DAYS
