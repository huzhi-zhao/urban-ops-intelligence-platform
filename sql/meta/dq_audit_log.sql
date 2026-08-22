-- Meta table (not a data product): the out-of-pipeline audit's own ledger.
-- Design: docs/dev/design/20260822-out-of-pipeline-dq-audit.md §5.1
-- Grain: (run_id, rule_id, table_name) — one row per check per run.
--
-- 🔴 APPEND-ONLY. This is the one table in the platform that is never dropped
-- and never rebuilt. The four-step whole-table rebuild in .claude/rules/gold-sql.md
-- R4 does NOT apply here: rebuilding this table erases the trend, and the trend
-- is the only reason it exists. Without it the audit can say "did today pass",
-- never "is this getting worse". Do not "unify" it with the Gold build.
--
-- 🔴 It lives in `uoip_meta`, not `uoip_gold`, and its DDL lives in sql/meta/,
-- not sql/ddl/ — both deliberate (design §5.2, launch §0.1):
--   * build_gold.TABLES, dq_baseline.py and dq_assertions.py all iterate "every
--     Gold table". A non-business table in that schema would need excluding in
--     three places, and the one that got missed would purge the audit log.
--   * sql/ddl/ is globbed by the contract/DDL/schema consistency test (which
--     would demand a contract this table by definition does not have) and by
--     ddl_parser, whose layer regex knows only silver|gold.
--
-- Created by: scripts/dq/audit_store.py (make dq-init). No contract, no seed.

CREATE TABLE IF NOT EXISTS dq_audit_log (
    -- not_null
    -- note: One audit run. Shared by every row the run produced, which is what
    -- makes "the previous run's observation" a well-defined thing to compare to.
    run_id VARCHAR,
    -- not_null
    -- accepted_values: ['daily', 'weekly', 'manual']
    cadence VARCHAR,
    -- not_null
    -- accepted_values: ['bronze', 'silver', 'gold']
    layer VARCHAR,
    -- not_null
    -- note: The table the rule observed. A rule declared `table: "*"` is expanded
    -- by the runner, so this column always names one real table.
    table_name VARCHAR,
    -- not_null
    -- note: config/dq/rules.yaml id. Renaming a rule starts a new trend rather
    -- than continuing the old one — that is intended, not a bug.
    rule_id VARCHAR,
    -- not_null
    -- accepted_values: ['structural', 'statistical', 'business', 'cross_layer']
    dimension VARCHAR,
    -- not_null
    -- accepted_values: ['error', 'warn']
    severity VARCHAR,
    -- note: What the check measured. NULL only when the check could not run —
    -- and a check that could not run is the one thing that raises (ADR 0012 §1).
    observed DOUBLE,
    -- note: Rendered as text because `between` carries two bounds and
    -- `pct_change` carries a tolerance, not a value.
    expected VARCHAR,
    -- not_null
    comparator VARCHAR,
    -- not_null
    passed BOOLEAN,
    -- note: The previous run's observation, copied in so a trend can be read off
    -- this table alone without a self-join. NULL on a rule's first ever run.
    previous_observed DOUBLE,
    -- note: Denominator, where the check has one (rows scanned).
    rows_checked BIGINT,
    -- note: Numerator, where the check has one (rows violating).
    rows_failed BIGINT,
    -- not_null
    elapsed_s DOUBLE,
    -- not_null
    checked_at TIMESTAMP(6),
    -- note: Free text for a check that could not run — the exception's message.
    error_text VARCHAR
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/meta/dq_audit_log/'
);
