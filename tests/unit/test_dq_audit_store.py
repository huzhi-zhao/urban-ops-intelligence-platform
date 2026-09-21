"""`uoip_meta.dq_audit_log`: SQL shape, isolation, and the append-only promise.

Offline — a fake DB-API connection records the statements. What is worth
nailing here is not that INSERT works but that nothing in this module can ever
drop or rebuild the table: the trend is the product (design §5.1).
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pytest

from scripts.ddl.apply_ddl import TrinoSettings
from scripts.dq import audit_store
from scripts.dq.audit_store import Observation, meta_schema, render_ddl

REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS = TrinoSettings(host="trino", port=8080, user="uoip", catalog="hive")


class FakeCursor:
    def __init__(self, log, rows):
        self._log = log
        self._rows = rows

    def execute(self, sql):
        self._log.append(sql)

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeConnection:
    def __init__(self, rows=()):
        self.statements: list[str] = []
        self._rows = list(rows)

    def cursor(self):
        return FakeCursor(self.statements, self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def an_observation(**overrides) -> Observation:
    base = {
        "run_id": "dq-1",
        "cadence": "daily",
        "layer": "gold",
        "table_name": "dim_plow_zone",
        "rule_id": "GOLD-ROWS-MIN-dim_plow_zone",
        "dimension": "statistical",
        "severity": "error",
        "observed": 25.0,
        "expected": ">= 22",
        "comparator": ">=",
        "passed": True,
        "elapsed_s": 0.2,
        "checked_at": dt.datetime(2026, 8, 22, 9, 0, 0),
    }
    base.update(overrides)
    return Observation(**base)


# ── the append-only promise ──────────────────────────────────────────────────


def test_the_module_contains_no_destructive_statement():
    """R4's four-step rebuild must never reach this table: rebuilding it erases
    the trend, which is the only reason it exists."""
    text = (REPO_ROOT / "scripts" / "dq" / "audit_store.py").read_text()
    executed = re.findall(r'f?"(?:CREATE|DROP|DELETE|TRUNCATE|INSERT)[^"]*"', text)
    for statement in executed:
        assert not re.match(r'f?"(DROP|DELETE|TRUNCATE)', statement), statement


def test_the_ddl_creates_only_if_not_exists():
    text = (REPO_ROOT / "sql" / "meta" / "dq_audit_log.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS dq_audit_log" in text


def test_the_audit_table_is_not_in_the_globbed_ddl_directory():
    """sql/ddl/ is globbed by the contract-consistency test and by ddl_parser,
    whose layer regex knows only silver|gold (launch §0.1)."""
    assert not (REPO_ROOT / "sql" / "ddl" / "dq_audit_log.sql").exists()
    assert (REPO_ROOT / "sql" / "meta" / "dq_audit_log.sql").exists()


def test_the_audit_table_is_invisible_to_the_three_gold_iterations():
    """design §5.2 / V5 — the isolation is what keeps a purge off the log."""
    from scripts.ddl.ddl_parser import load_all_ddl
    from scripts.gold.build_gold import TABLES

    assert "dq_audit_log" not in {t.name for t in TABLES}
    assert "dq_audit_log" not in load_all_ddl()
    assert len(TABLES) == 17


# ── schema and location ──────────────────────────────────────────────────────


def test_meta_schema_is_separate_from_gold():
    assert meta_schema() == "uoip_meta"


def test_a_prefix_isolates_both_the_schema_and_the_path():
    assert meta_schema("smoke-20260822") == "uoip_meta_smoke_20260822"
    rendered = render_ddl(
        (REPO_ROOT / "sql" / "meta" / "dq_audit_log.sql").read_text(), "uoip", "smoke-20260822"
    )
    assert "s3a://uoip/smoke-20260822/meta/dq_audit_log/" in rendered


def test_render_strips_the_header_and_the_trailing_semicolon():
    rendered = render_ddl(
        (REPO_ROOT / "sql" / "meta" / "dq_audit_log.sql").read_text(), "uoip"
    )
    assert rendered.startswith("CREATE TABLE")
    assert not rendered.endswith(";")


def test_ensure_table_creates_the_schema_at_the_meta_location(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(audit_store, "_connect", lambda *a, **k: connection)
    schema = audit_store.ensure_table(SETTINGS, "uoip")
    assert schema == "uoip_meta"
    assert "CREATE SCHEMA IF NOT EXISTS hive.uoip_meta" in connection.statements[0]
    assert "s3a://uoip/meta/" in connection.statements[0]
    assert connection.statements[1].startswith("CREATE TABLE IF NOT EXISTS dq_audit_log")


# ── append ───────────────────────────────────────────────────────────────────


def test_insert_lists_every_column_explicitly():
    sql = audit_store.insert_sql("uoip_meta", [an_observation()])
    assert "SELECT *" not in sql
    for column in audit_store.COLUMNS:
        assert column in sql


def test_insert_renders_null_boolean_and_timestamp_literals():
    sql = audit_store.insert_sql(
        "uoip_meta", [an_observation(passed=False, previous_observed=None, error_text=None)]
    )
    assert "false" in sql
    assert "NULL" in sql
    assert "TIMESTAMP '2026-08-22 09:00:00.000000'" in sql


def test_insert_escapes_a_quote_in_free_text():
    sql = audit_store.insert_sql("uoip_meta", [an_observation(error_text="it's broken")])
    assert "'it''s broken'" in sql


def test_append_of_nothing_writes_nothing():
    connection = FakeConnection()
    assert audit_store.append(connection, "uoip_meta", []) == 0
    assert connection.statements == []


def test_append_returns_the_row_count():
    connection = FakeConnection()
    assert audit_store.append(connection, "uoip_meta", [an_observation(), an_observation()]) == 2
    assert len(connection.statements) == 1


# ── trend lookup ─────────────────────────────────────────────────────────────


def test_previous_observations_are_keyed_by_rule_and_table():
    """A `table: "*"` rule has one trend per table; collapsing them would
    compare dim_channel against fact_recommendation."""
    connection = FakeConnection(rows=[("GOLD-ROWS-DRIFT", "dim_channel", 15.0)])
    previous = audit_store.previous_observations(connection, "uoip_meta")
    assert previous == {("GOLD-ROWS-DRIFT", "dim_channel"): 15.0}


def test_previous_observations_ignore_checks_that_could_not_run():
    """Otherwise a broken run becomes the baseline the next run is judged
    against."""
    assert "observed IS NOT NULL" in audit_store.PREVIOUS_SQL


def test_previous_observations_take_the_most_recent_per_rule():
    assert "ORDER BY checked_at DESC" in audit_store.PREVIOUS_SQL
    assert "recency = 1" in audit_store.PREVIOUS_SQL


@pytest.mark.parametrize("attribute", ["could_not_run"])
def test_a_check_that_errored_is_marked_as_unable_to_run(attribute):
    assert an_observation(error_text="boom").could_not_run
    assert not an_observation().could_not_run
