-- Meta table (not a data product): the verdict one audit run reached about the
-- Gold batch as a whole.
-- Design: docs/dev/design/20260822-cross-layer-reconciliation-and-certification.md §4
-- Grain: (run_id) — one row per audit run, NOT one row per table. What gets
-- certified is *this batch of Gold*, not a table at a time.
--
-- 🔴 APPEND-ONLY, exactly like dq_audit_log beside it. The four-step
-- whole-table rebuild in .claude/rules/gold-sql.md R4 does NOT apply: the
-- previous verdicts are the product. `run_id` carries foreign-key semantics
-- against dq_audit_log.run_id so a reader can walk from the conclusion back to
-- the 81+ observations that produced it.
--
-- 🔴 It lives in `uoip_meta`, not `uoip_gold`. build_gold.TABLES,
-- dq_baseline.py and dq_assertions.py each iterate "every Gold table" and must
-- keep seeing **17**; a non-business table in that schema would need excluding
-- in three places and the one that got missed would purge this ledger.
--
-- 🔴 THREE statuses, not two. `suspect` means "we looked, the data is wrong";
-- `unknown` means "we could not look". Collapsing them hands out a
-- conclusion-shaped answer on the day the audit itself was broken — and
-- "no suspect row today" reads as "no problem" to everyone downstream.
--
-- Created by: scripts/dq/certify.py (make dq-certify). No contract, no seed.

CREATE TABLE IF NOT EXISTS gold_certification (
    -- not_null
    -- note: The dq_audit_log run this verdict summarises. Foreign-key
    -- semantics, not enforced by the engine — Hive has no constraints.
    run_id VARCHAR,
    -- not_null
    -- accepted_values: ['daily', 'weekly', 'manual']
    cadence VARCHAR,
    -- not_null
    certified_at TIMESTAMP(6),
    -- not_null
    -- accepted_values: ['certified', 'suspect', 'unknown']
    -- note: certified = every error-level check passed and nothing failed to
    -- run. warn-level findings do NOT block it (design §0.3) but they are
    -- counted below, so "green with warnings" stays visible.
    status VARCHAR,
    -- not_null
    -- note: error-level findings. Any non-zero value forces `suspect`.
    error_count INTEGER,
    -- not_null
    -- note: warn-level findings. Never affects `status` — by design.
    warn_count INTEGER,
    -- not_null
    -- note: Denominator. Without it a run that executed 3 checks and a run
    -- that executed 81 look identical in this table.
    checks_total INTEGER,
    -- not_null
    -- note: Checks that produced no number at all. Any non-zero value forces
    -- `unknown`, which outranks `suspect`.
    checks_could_not_run INTEGER,
    -- note: One human-readable sentence. Free text.
    note VARCHAR
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/meta/gold_certification/'
);
