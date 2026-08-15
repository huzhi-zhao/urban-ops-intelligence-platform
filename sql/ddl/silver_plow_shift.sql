-- Silver contract: contracts/silver-contracts/silver_plow_shift.yaml
-- Grain: shift
-- Row count expectation: {'exact': 418}
--   19 events x 22 zones, zero missing. Any deviation is an upstream schema change to
-- escalate.
-- Primary key (informational only — Trino does not enforce PK/FK/UNIQUE): (id)
-- unique: [['id']]
-- accepted_values (shift_number): [1, 2, 3, 4, 5]
-- relationships:
--   COUNT(*) = 418
--   COUNT(DISTINCT plow_zone) = 22

CREATE TABLE IF NOT EXISTS silver_plow_shift (
    -- not_null
    id VARCHAR,
    -- not_null
    -- relationships -> silver_parking_ban.id
    -- note: Left-join direction only. 30/49 bans have no matching shift — not a defect, see
    -- BO-2.
    snow_ban_id VARCHAR,
    -- not_null
    -- accepted_values: [1, 2, 3, 4, 5]
    -- note: The core supply-side quantity. Ordering by this matches ordering by shift_start
    -- exactly (rho = 1.0, all 19 events).
    shift_number INTEGER,
    -- not_null
    shift_start_utc TIMESTAMP(6),
    -- not_null
    -- note: Planned end of a 12h template window, not a completion time. Identical across all
    -- zones in the same shift_number — ADR 0008.
    shift_end_utc TIMESTAMP(6),
    -- not_null
    -- cardinality: 22
    -- note: 22 of 25 boundary zones. B/D, X, Downtown never appear — they have no scheduled
    -- shift, not a 'last shift' assignment.
    plow_zone VARCHAR,
    -- not_null
    source_id VARCHAR,
    -- not_null
    loaded_at TIMESTAMP(6)
)
WITH (
    format = 'PARQUET',
    external_location = 's3a://{bucket}/silver/plow_shift/'
);
