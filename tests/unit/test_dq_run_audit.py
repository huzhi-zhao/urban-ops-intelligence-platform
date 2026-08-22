"""The audit runner: rule expansion, check semantics, and what fails a run.

Offline. The two behaviours worth nailing are the ones the design spends most
of its ink on: a *finding* must not fail the run (ADR 0012 §1 rule 2), and no
Silver read may go unwindowed (O13's wall).
"""

from __future__ import annotations

import datetime as dt

import pytest

from scripts.dq import run_audit
from scripts.dq.audit_store import Observation
from scripts.dq.rules import Rule
from scripts.gold.build_gold import TABLES


class FakeReport:
    def __init__(self, rows: int, nulls: dict[str, int], elapsed: float = 0.1):
        self.rows = rows
        self.nulls = nulls
        self.elapsed = elapsed

    @property
    def all_null(self):
        return [c for c, n in self.nulls.items() if self.rows and n == self.rows]

    def rate(self, nulls: int) -> float:
        return 0.0 if not self.rows else 100.0 * nulls / self.rows


class FakeConnection:
    def __init__(self, scalar=0):
        self.executed: list[str] = []
        self._scalar = scalar

    def cursor(self):
        return self

    def execute(self, sql):
        self.executed.append(sql)

    def fetchone(self):
        return (self._scalar,)

    def fetchall(self):
        return [(self._scalar,)]


def a_rule(**overrides) -> Rule:
    base = {
        "id": "R1",
        "layer": "gold",
        "table": "dim_plow_zone",
        "dimension": "statistical",
        "check": {"type": "row_count"},
        "comparator": ">=",
        "expected": 22,
        "severity": "error",
        "cadence": "daily",
    }
    base.update(overrides)
    return Rule(**base)


def a_context(connection=None, reports=None) -> run_audit.Context:
    ctx = run_audit.Context(
        connection=connection or FakeConnection(),
        silver_schema="uoip_silver",
        window_start=dt.date(2026, 8, 8),
        window_end=dt.date(2026, 8, 23),
        profiles=reports or {},
    )
    return ctx


# ── expansion ────────────────────────────────────────────────────────────────


def test_a_named_table_expands_to_itself():
    assert run_audit.expand([a_rule()]) == [(a_rule(), "dim_plow_zone")]


def test_a_star_rule_expands_to_every_gold_table():
    pairs = run_audit.expand([a_rule(table="*")])
    assert [t for _, t in pairs] == [t.name for t in TABLES]
    assert len(pairs) == 17


def test_the_two_whole_schema_checks_produce_one_row_not_seventeen():
    """"How many tables are empty" is one number about all of them; expanding
    it per table would log the same answer seventeen times."""
    for kind in ("empty_tables", "all_null_columns"):
        pairs = run_audit.expand([a_rule(table="*", check={"type": kind})])
        assert pairs == [(a_rule(table="*", check={"type": kind}), "*")]


# ── checks ───────────────────────────────────────────────────────────────────


def test_row_count_comes_from_the_shared_profile_scan():
    ctx = a_context(reports={"dim_plow_zone": FakeReport(25, {"plow_zone": 0})})
    observed, rows_checked, _ = run_audit.run_check(a_rule(), "dim_plow_zone", ctx, set())
    assert (observed, rows_checked) == (25.0, 25)


def test_null_rate_of_a_named_column_is_a_percentage():
    ctx = a_context(reports={"dim_plow_zone": FakeReport(25, {"area_delta_pct": 17})})
    rule = a_rule(check={"type": "null_rate", "column": "area_delta_pct"})
    observed, _, rows_failed = run_audit.run_check(rule, "dim_plow_zone", ctx, set())
    assert observed == pytest.approx(68.0)
    assert rows_failed == 17


def test_a_missing_column_is_a_check_that_could_not_run_not_a_failure():
    ctx = a_context(reports={"dim_plow_zone": FakeReport(25, {})})
    rule = a_rule(check={"type": "null_rate", "column": "gone"})
    with pytest.raises(run_audit.CheckError, match="no column"):
        run_audit.run_check(rule, "dim_plow_zone", ctx, set())


def test_the_catch_all_null_rule_skips_columns_that_have_their_own_rule():
    """🔴 Otherwise the seven documented-NULL columns all report violations and
    the baseline gets muted (design §4.1)."""
    ctx = a_context(reports={"dim_plow_zone": FakeReport(25, {"area_delta_pct": 17, "ok": 0})})
    rule = a_rule(check={"type": "null_rate", "scope": "unlisted_columns"}, comparator="==",
                  expected=0)
    listed = {("dim_plow_zone", "area_delta_pct")}
    observed, _, _ = run_audit.run_check(rule, "dim_plow_zone", ctx, listed)
    assert observed == 0.0

    unguarded, _, _ = run_audit.run_check(rule, "dim_plow_zone", ctx, set())
    assert unguarded == 1.0


def test_freshness_reads_the_partitions_metadata_not_the_data():
    """`$partitions` is Metastore-only — the whole point on a 4,878-partition
    table (gold-sql.md, O13)."""
    connection = FakeConnection(scalar=dt.date.today() - dt.timedelta(days=1))
    ctx = a_context(connection=connection)
    rule = a_rule(
        layer="silver",
        table="silver_service_request",
        check={"type": "partition_freshness", "partition_column": "open_date_local"},
        comparator="<=",
        expected=2,
    )
    observed, _, _ = run_audit.run_check(rule, "silver_service_request", ctx, set())
    assert observed == 1.0
    assert '"silver_service_request$partitions"' in connection.executed[0]
    assert "uoip_silver" in connection.executed[0]


def test_a_table_with_no_partitions_could_not_be_checked():
    ctx = a_context(connection=FakeConnection(scalar=None))
    rule = a_rule(check={"type": "partition_freshness", "partition_column": "open_date_local"})
    with pytest.raises(run_audit.CheckError, match="no partitions"):
        run_audit.run_check(rule, "silver_service_request", ctx, set())


def test_a_sql_check_gets_the_silver_schema_and_the_window_substituted():
    connection = FakeConnection(scalar=99.9)
    ctx = a_context(connection=connection)
    rule = a_rule(
        check={
            "type": "sql",
            "sql": "SELECT 1 FROM {{ silver }}.silver_service_request "
                   "WHERE open_date_local >= DATE '{window_start}' "
                   "AND open_date_local < DATE '{window_end}'",
        }
    )
    observed, _, _ = run_audit.run_check(rule, "silver_service_request", ctx, set())
    assert observed == pytest.approx(99.9)
    sql = connection.executed[0]
    assert "uoip_silver.silver_service_request" in sql
    assert "'2026-08-08'" in sql and "'2026-08-23'" in sql
    assert "{" not in sql


def test_a_sql_check_takes_a_second_column_as_the_denominator():
    """A rate with no rows_checked is unreadable — 100% over three rows and
    100% over 300,000 look identical in the log (found on the first production
    run, where the spatial hit rate reported a bare 100)."""

    class TwoColumn(FakeConnection):
        def fetchone(self):
            return (99.9, 237867)

    ctx = a_context(connection=TwoColumn())
    rule = a_rule(check={"type": "sql", "sql": "SELECT 1, 2"})
    observed, rows_checked, _ = run_audit.run_check(rule, "t", ctx, set())
    assert (observed, rows_checked) == (pytest.approx(99.9), 237867)


def test_a_single_column_sql_check_still_works():
    ctx = a_context(connection=FakeConnection(scalar=908))
    rule = a_rule(check={"type": "sql", "sql": "SELECT 1"})
    assert run_audit.run_check(rule, "t", ctx, set()) == (908.0, None, None)


def test_a_trino_error_becomes_could_not_run_rather_than_a_silent_pass():
    class Exploding(FakeConnection):
        def execute(self, sql):
            raise RuntimeError("Read timed out")

    ctx = a_context(connection=Exploding())
    rule = a_rule(check={"type": "sql", "sql": "SELECT 1"})
    with pytest.raises(run_audit.CheckError, match="Read timed out"):
        run_audit.run_check(rule, "x", ctx, set())


# ── a whole run ──────────────────────────────────────────────────────────────


def test_a_run_logs_one_observation_per_expanded_rule(monkeypatch):
    ctx = a_context(reports={"dim_plow_zone": FakeReport(25, {"plow_zone": 0})})
    results = run_audit.audit(ctx, [a_rule()], "daily", {})
    assert len(results) == 1
    assert results[0].passed and results[0].observed == 25.0
    assert results[0].rule_id == "R1"


def test_a_run_carries_the_previous_observation_onto_the_row():
    ctx = a_context(reports={"dim_plow_zone": FakeReport(25, {"plow_zone": 0})})
    rule = a_rule(comparator="pct_change", expected=10)
    results = run_audit.audit(ctx, [rule], "daily", {("R1", "dim_plow_zone"): 25.0})
    assert results[0].previous_observed == 25.0
    assert results[0].passed


def test_a_failing_check_is_recorded_not_raised():
    ctx = a_context(reports={"dim_plow_zone": FakeReport(3, {"plow_zone": 0})})
    results = run_audit.audit(ctx, [a_rule()], "daily", {})
    assert results[0].passed is False
    assert results[0].could_not_run is False


def test_a_broken_check_is_marked_and_carries_its_message():
    ctx = a_context(reports={"dim_plow_zone": FakeReport(25, {})})
    rule = a_rule(check={"type": "null_rate", "column": "gone"})
    results = run_audit.audit(ctx, [rule], "daily", {})
    assert results[0].could_not_run
    assert "no column" in results[0].error_text


def test_rules_outside_the_cadence_are_not_run():
    ctx = a_context(reports={"dim_plow_zone": FakeReport(25, {"plow_zone": 0})})
    assert run_audit.audit(ctx, [a_rule(cadence="weekly")], "daily", {}) == []
    assert len(run_audit.audit(ctx, [a_rule(cadence="weekly")], "manual", {})) == 1


def test_one_run_id_covers_every_row_of_the_run():
    ctx = a_context(reports={t.name: FakeReport(10, {"c": 0}) for t in TABLES})
    results = run_audit.audit(ctx, [a_rule(table="*")], "daily", {})
    assert len({r.run_id for r in results}) == 1


# ── reporting ────────────────────────────────────────────────────────────────


def test_the_summary_separates_findings_from_a_broken_audit():
    results = [
        Observation("r", "daily", "gold", "t", "A", "statistical", "error", 1.0, ">= 2", ">=",
                    False),
        Observation("r", "daily", "gold", "t", "B", "statistical", "warn", 1.0, ">= 2", ">=",
                    False),
        Observation("r", "daily", "gold", "t", "C", "statistical", "error", None, ">= 2", ">=",
                    False, error_text="boom"),
    ]
    summary = run_audit.summarise(results, "daily")
    assert "1 error · 1 warn · 1 could not run" in summary
    assert "boom" in summary


def test_expected_is_rendered_so_a_log_row_reads_on_its_own():
    assert run_audit.render_expected(a_rule()) == ">= 22"
    assert "±10%" in run_audit.render_expected(a_rule(comparator="pct_change", expected=10))
    assert "points" in run_audit.render_expected(
        a_rule(comparator="pct_point_change", expected=5)
    )
    assert "between 1 and 2" == run_audit.render_expected(
        a_rule(comparator="between", expected=[1, 2])
    )


# ── exit codes: the whole "does not block" claim, made checkable ─────────────


@pytest.fixture()
def offline_main(monkeypatch):
    """Wire main() to a fake Trino so only its exit-code logic is exercised."""
    from scripts.ddl import apply_ddl

    monkeypatch.setenv("S3_BUCKET_NAME", "uoip")
    monkeypatch.setattr(run_audit, "load_trino_settings", lambda: apply_ddl.TrinoSettings(
        host="trino", port=8080, user="uoip", catalog="hive"))
    monkeypatch.setattr(run_audit, "_connect", lambda *a, **k: FakeConnection())
    monkeypatch.setattr(run_audit.audit_store, "ensure_table", lambda *a, **k: "uoip_meta")
    monkeypatch.setattr(run_audit.audit_store, "previous_observations", lambda *a: {})
    monkeypatch.setattr(run_audit.audit_store, "append", lambda *a: len(a[2]))
    monkeypatch.setattr(run_audit, "notify", lambda text: True)

    def set_results(results):
        monkeypatch.setattr(run_audit, "audit", lambda *a, **k: results)

    return set_results


def a_result(passed=True, error=None) -> Observation:
    return Observation(
        "run", "daily", "gold", "dim_plow_zone", "R1", "statistical", "error",
        None if error else 1.0, ">= 2", ">=", passed, error_text=error,
    )


def test_findings_do_not_fail_the_run(offline_main):
    """🔴 ADR 0012 §1 rule 2. A run that went red on a finding would stay red
    until someone repaired the data by hand — which is how a DAG gets muted."""
    offline_main([a_result(passed=False), a_result(passed=False)])
    assert run_audit.main([]) == 0


def test_a_check_that_could_not_run_does_fail_the_run(offline_main):
    offline_main([a_result(passed=False, error="Read timed out")])
    assert run_audit.main([]) == 2


def test_a_clean_run_exits_zero(offline_main):
    offline_main([a_result()])
    assert run_audit.main([]) == 0


def test_a_dry_run_appends_nothing(offline_main, monkeypatch):
    appended: list = []
    monkeypatch.setattr(run_audit.audit_store, "append", lambda *a: appended.append(a))
    offline_main([a_result()])
    assert run_audit.main(["--dry-run"]) == 0
    assert appended == []


def test_findings_are_pushed_to_discord(offline_main, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(run_audit, "notify", lambda text: sent.append(text) or True)
    offline_main([a_result(passed=False)])
    run_audit.main([])
    assert sent and "1 error" in sent[0]


def test_a_clean_run_stays_quiet(offline_main, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(run_audit, "notify", lambda text: sent.append(text) or True)
    offline_main([a_result()])
    run_audit.main([])
    assert sent == []


def test_full_sweep_is_the_manual_cadence(offline_main, monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(run_audit, "audit", lambda ctx, rules, cadence, prev: (
        seen.update(c=cadence) or []
    ))
    run_audit.main(["--full-sweep"])
    assert seen["c"] == "manual"
