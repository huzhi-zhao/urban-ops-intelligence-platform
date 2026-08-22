"""Build the Gold tables from Silver plus the seed CSVs.

Execution model — measured on Trino 451 / Hive connector, 2026-08-19 (O12):
a Gold table is rebuilt whole, in four steps, because three of the four
obvious ways do not exist on this connector (CREATE OR REPLACE, TRUNCATE and
DELETE are all NOT_SUPPORTED):

    1. DROP TABLE
    2. purge the table's storage prefix   <- not optional, see below
    3. CREATE TABLE from sql/ddl/<table>.sql
    4. INSERT INTO <table> (<explicit columns>) <the SELECT in sql/dml/>

Step 2 is load-bearing: these are *external* tables, so DROP leaves the
objects behind and a table recreated over them reads the previous generation
immediately. Skipping it makes a rebuild silently union two generations.
Because step 4 appends, the exact row-count gates are the only thing that
notices — which is why a failed gate fails the whole batch.

See .claude/rules/gold-sql.md R4 and
docs/dev/design/20260819-gold-dimensional-build.md §4.3/§7.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

# Reused rather than re-derived, same as dags/_alerts.py: redaction (the
# webhook URL *is* the credential), Discord's 2000-char `content` ceiling, and
# the short timeout that keeps alerting from becoming the thing that hangs.
from ingestion.snapshot.notify import CONTENT_LIMIT, TIMEOUT_SECS, _redact
from scripts._env import load_cli_env
from scripts.ddl.apply_ddl import (
    LAYERS,
    TrinoConfigError,
    _connect,
    _purge_storage,
    load_trino_settings,
    normalise_prefix,
    render_ddl,
    schema_name,
)
from scripts.ddl.ddl_parser import DDL_DIR, parse_ddl_file
from scripts.gold.forecast_artefacts import PREDICTION_COLUMNS, Artefact, load_artefacts
from scripts.gold.gates import Gate, parse_gates

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DML_DIR = REPO_ROOT / "sql" / "dml"
INTELLIGENCE_DIR = REPO_ROOT / "sql" / "intelligence"
SEED_DIR = REPO_ROOT / "config" / "seeds"
M1_CONFIG = REPO_ROOT / "config" / "models" / "m1.yaml"


def _prediction_cells() -> int:
    """F5's rows-per-model_version, read from m1.yaml rather than written twice.

    The trainer gates its own prediction panel on this same key, so a change
    to the scheduling era moves both ends together or fails loudly at whichever
    one runs first. A literal here would drift silently.
    """
    import yaml

    with M1_CONFIG.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    return int(config["panel"]["expected_prediction_cells"])


PREDICTION_CELLS = _prediction_cells()

LINEAGE_COLUMNS = ("etl_run_id", "built_at", "source_max_ingest_date")

# How to reach Trino from a human shell. The .env value is the *container*
# view (TRINO_HOST=trino) because the Airflow container is the unattended
# consumer; a host shell has to go through the published port. Printing this
# on a connection failure is not a nicety: the real cause arrives buried under
# five layers of urllib3 traceback, which cost a round trip on 2026-08-19.
HOST_SHELL_HINT = (
    "Cannot reach Trino. From the compute node's *host* shell, Trino is on the\n"
    "published port, not the in-network one — prefix the command:\n"
    "    TRINO_HOST=localhost TRINO_PORT=8090 make gold-build\n"
    "(.env holds the container view, trino:8080, for the Airflow container.)"
)

# Same webhook the backfill plan scripts use (_plan_lib.sh) and the same
# fallback order — one Discord channel for all UOIP long-running jobs, per
# dags/_alerts.py. A build is manually triggered from a terminal someone is
# not necessarily watching (dim_service_type alone is 19 sequential chunks),
# so a run that clears this threshold gets a completion notice the same way a
# backfill window failure does. Below the threshold the terminal output is
# still right there, so a notification would just be noise.
ALERT_URL_VARS = ("BACKFILL_ALERT_WEBHOOK_URL", "SNAPSHOT_ALERT_WEBHOOK_URL")
SLOW_BUILD_THRESHOLD_SECS = 300


def _resolve_alert_url() -> str:
    for var in ALERT_URL_VARS:
        url = (os.environ.get(var) or "").strip()
        if url:
            return url
    return ""


def notify_build_outcome(text: str, elapsed_secs: float) -> bool:
    """POST a Discord notice about a build that took long enough to walk away from.

    Mirrors dags/_alerts.py's failure notice — same payload shape, same
    never-raises contract — but covers what that module does not: this script
    is run by hand from a compute-node shell, where "did it finish, and did it
    pass" is otherwise answered only by staring at a terminal.

    🔴 Every outcome notifies, not just success. The first 18-minute run of
    `--only dims` ended on a failed gate and sent nothing, which is precisely
    the case the notification exists for — a run long enough to walk away from
    is a run whose *failure* you most need pushed to you. A crash notifies for
    the same reason: `dim_service_type` is 19 sequential chunks, and dying at
    chunk 18 is a ten-minute silence otherwise.

    The elapsed-time threshold is the only filter: below it the terminal
    output is still on screen, so a notification would be noise.

    Returns True if delivered; never raises, since a failing alert channel
    must not turn into a failing build.
    """
    url = _resolve_alert_url()
    if not url:
        logger.warning(
            "Build took %.0fs (>= %ds) and none of %s is set — no notice was sent: %s",
            elapsed_secs, SLOW_BUILD_THRESHOLD_SECS, "/".join(ALERT_URL_VARS), text,
        )
        return False
    payload = {"content": text[:CONTENT_LIMIT], "allowed_mentions": {"parse": []}}
    try:
        response = requests.post(url, json=payload, timeout=TIMEOUT_SECS)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error("Could not deliver build notice: %s", _redact(e))
        return False
    return True


@dataclass(frozen=True)
class Table:
    """One Gold table and what it needs built before it."""

    name: str
    stage: str  # "seeds" | "dims" | "facts"
    deps: tuple[str, ...] = ()
    seed: str | None = None  # basename in config/seeds/, for seed-loaded tables
    # Built from object-storage artefacts rather than from Silver. Only
    # fact_request_forecast: its rows are a training output, and the design
    # §5 resolution to the "rebuild whole vs never overwrite a version"
    # conflict is that the artefacts are the record and the table is a view
    # of them. See scripts/gold/forecast_artefacts.py.
    from_artefacts: bool = False
    # The scoring chain's SELECTs live in sql/intelligence/, not sql/dml/. Same
    # mechanism, different directory: design §6 keeps the intelligence layer
    # visibly separate from the dimensional loads.
    intelligence: bool = False
    # Rebuilt once per model_version present in fact_request_forecast, with
    # {model_version} substituted into each chunk. F7 only: F6's grain has no
    # model_version and is scored against a single serving version.
    per_model_version: bool = False
    # Gates that cannot be expressed as COUNT(*) in the DDL header. Each entry
    # is (description, SQL returning one number, expected value) and an
    # optional fourth element ">=" turning the comparison into a lower bound.
    #
    # A lower bound is the right shape for exactly one kind of gate: a number
    # measured off a *live* upstream, which drifts for reasons that are not
    # build defects. Everything that detects a broken build — a failed purge
    # doubling the rows, a chunk that never ran — stays an equality, because
    # those numbers are properties of the pipeline and do not move on their
    # own. Do not reach for ">=" to quiet a gate that is telling you something.
    extra_gates: tuple[tuple[str, str, int] | tuple[str, str, int, str], ...] = field(default=())


# Order is hardcoded rather than derived from the filenames: `dim_service_type`
# sorting after `dim_winter_category` is a coincidence, and `dim_admin_label`
# does not sort where it must run. Dependency graph: design §5.
TABLES: tuple[Table, ...] = (
    Table("dim_winter_category", "seeds", seed="winter_category.csv"),
    Table("dim_channel", "seeds", seed="channel.csv"),
    Table(
        "dim_recommendation_rules",
        "seeds",
        seed="recommendation_rules.csv",
        extra_gates=(
            (
                "at least one fallback rule exists (design §6.9)",
                # LEAST(...,1) turns "at least one" into something an equality
                # gate can state. A rule set with no fallback degrades to empty
                # attribution text in L3 rather than saying it degraded.
                "SELECT LEAST(COUNT(*), 1) FROM dim_recommendation_rules WHERE is_fallback = true",
                1,
            ),
        ),
    ),
    Table(
        "dim_service_type",
        "dims",
        deps=("dim_winter_category",),
        extra_gates=(
            (
                "every observed silver_service_request.type resolves to a row "
                "(build-time LEFT ANTI JOIN, design §6.2 decision 1)",
                # NB: this is about *coverage of type values*, not about
                # winter_category being non-null. A non-winter type with a NULL
                # winter_category is correct; conflating the two makes the
                # first build fail forever.
                "SELECT COUNT(*) FROM ("
                ' SELECT DISTINCT s."type" FROM {silver}.silver_service_request s'
                ' LEFT JOIN dim_service_type d ON d."type" = s."type"'
                ' WHERE d."type" IS NULL)',
                0,
            ),
        ),
    ),
    Table("dim_plow_zone", "dims"),
    Table("dim_admin_label", "dims"),
    Table("dim_snowfall_event", "dims"),
    Table("dim_plow_event", "dims", deps=("dim_snowfall_event",)),
    Table(
        "dim_region_crosswalk",
        "dims",
        deps=("dim_plow_zone", "dim_admin_label", "dim_service_type"),
        extra_gates=(
            (
                "weights sum to 1.0 within every (plow_zone, label_type) group",
                "SELECT COUNT(*) FROM ("
                " SELECT plow_zone, label_type, SUM(weight) AS w FROM dim_region_crosswalk"
                " GROUP BY plow_zone, label_type) WHERE ABS(w - 1.0) > 1e-9",
                0,
            ),
            (
                "exactly one is_dominant row per (plow_zone, label_type) group",
                "SELECT COUNT(*) FROM ("
                " SELECT plow_zone, label_type, COUNT_IF(is_dominant) AS n"
                " FROM dim_region_crosswalk GROUP BY plow_zone, label_type) WHERE n <> 1",
                0,
            ),
        ),
    ),
    Table("fact_plow_shift", "facts", deps=("dim_plow_event", "dim_plow_zone")),
    Table("fact_parking_ban", "facts", deps=("dim_plow_event",)),
    Table(
        "fact_event_zone_rank",
        "facts",
        deps=("dim_plow_event", "dim_plow_zone"),
        extra_gates=(
            (
                # The DDL states these two as prose (COUNT(DISTINCT ...) is not
                # a shape parse_gates executes), and they are the B1 fan-out
                # guard: two plow operations collapsing onto one snowfall event
                # would double L3's F6 join without anything raising. Asserting
                # both sides at 17 says "17 operations align, to 17 distinct
                # events" in the only form an equality gate can.
                "17 operations carry a matched snowfall event (>= 17/19, design §6.10)",
                "SELECT COUNT(DISTINCT plow_event_id) FROM fact_event_zone_rank"
                " WHERE matched_snowfall_event_id IS NOT NULL",
                17,
            ),
            (
                "fan-out guard: those 17 map to 17 distinct snowfall events (B1)",
                "SELECT COUNT(DISTINCT matched_snowfall_event_id) FROM fact_event_zone_rank",
                17,
            ),
        ),
    ),
    Table(
        "fact_service_request_zone_event",
        "facts",
        deps=("dim_snowfall_event", "dim_plow_zone", "dim_winter_category", "dim_service_type"),
        extra_gates=(
            (
                # COUNT(*) = 13068 is parsed from the DDL header; these three
                # are not (COUNT(DISTINCT ...) is prose to parse_gates) and
                # they are what distinguishes a full panel from a table that
                # merely has the right number of rows. A chunk that failed to
                # run drops whole events, which 2178 catches and 13068 also
                # catches — but 916 is the only one that says the *counts*
                # landed, not just the skeleton.
                "full panel: 2,178 (event, zone) cells = 22 zones x 99 events",
                "SELECT COUNT(*) FROM (SELECT DISTINCT snowfall_event_id, plow_zone"
                " FROM fact_service_request_zone_event)",
                2_178,
            ),
            (
                "1,298 of those cells are in the scheduling era (F6's subset)",
                "SELECT COUNT(*) FROM (SELECT DISTINCT snowfall_event_id, plow_zone"
                " FROM fact_service_request_zone_event WHERE snowfall_event_id IN"
                " (SELECT snowfall_event_id FROM dim_snowfall_event"
                " WHERE is_scheduling_era = true))",
                1_298,
            ),
            (
                # 🔴 A lower bound, not the 916 the design carries. 916 was
                # measured off the *live* Socrata API on 2026-08-09, and the
                # probe that produced it no longer reproduces it: 69.8% on
                # 2026-08-19, against 908 (69.95%) measured here. The drift is
                # not in the requests — the upstream has *more* rows now — it
                # is in the event boundaries: Open-Meteo revises its archive,
                # segment_events re-cuts the runs, and a cell's days stop
                # falling inside its event. Event count, era count and median
                # duration all stay put while that happens, so nothing else
                # shows it.
                #
                # Equality here would gate every future build on how the
                # upstream looked one day in August. The three numbers that do
                # catch a broken build (13,068 / 2,178 / 1,298) are equalities
                # and stay that way. Launch doc §4.9.
                "scheduling-era cells with at least one request "
                "(908 measured 2026-08-19; design's 916 is stale, see §4.9)",
                "SELECT COUNT(*) FROM (SELECT snowfall_event_id, plow_zone"
                " FROM fact_service_request_zone_event WHERE snowfall_event_id IN"
                " (SELECT snowfall_event_id FROM dim_snowfall_event"
                " WHERE is_scheduling_era = true)"
                " GROUP BY snowfall_event_id, plow_zone HAVING SUM(request_count) > 0)",
                880,
                ">=",
            ),
            (
                # Same reasoning as F8's chunk gate: a chunked build that dies
                # halfway leaves a smaller, entirely plausible table.
                "every chunk contributed: all 99 events present",
                "SELECT COUNT(DISTINCT snowfall_event_id) FROM fact_service_request_zone_event",
                99,
            ),
        ),
    ),
    # Chunked: the only Gold table whose grain contains a date, and the only
    # one whose SELECT spans the whole Silver history. R1/R2 of gold-sql.md.
    Table(
        "fact_winter_request_daily_by_label",
        "facts",
        deps=("dim_admin_label", "dim_service_type"),
        extra_gates=(
            (
                # 141,377 measured 2026-08-19 by counting the real
                # (date, label) pairs. The design's ≈1.6 M assumed a dense
                # 6,600-day x 252-label panel; only ~80% of winter rows carry
                # a label at all, and the panel is nowhere near dense.
                "row count matches the measured (date, label) pair count (O14)",
                "SELECT COUNT(*) FROM fact_winter_request_daily_by_label",
                141_377,
            ),
            (
                # A chunked build that dies halfway leaves a smaller, entirely
                # plausible-looking table. The chunk count is the only thing
                # that says otherwise.
                # 18, not 19: the chunk range is 2008..2026 but **2008
                # contributes nothing** — measured 2026-08-19, the years run
                # 2009..2026 at 3,938-16,409 rows each. Silver has 2008 day
                # partitions; none of their rows is both a winter `type` and
                # carrying an admin label. The 19 was inferred from the chunk
                # count and never measured, which is the exact mistake this
                # gate exists to catch.
                "every chunk contributed: 18 calendar years present (2009..2026)",
                'SELECT COUNT(DISTINCT YEAR("date")) FROM fact_winter_request_daily_by_label',
                18,
            ),
        ),
    ),
    # ── scoring ───────────────────────────────────────────────────────────
    # Built from s3://{bucket}/gold/_forecast_runs/, not from Silver or a seed.
    # It is its own stage so that `--only facts` — the rebuild L2 runs after a
    # Silver repair — cannot touch it: F5's inputs are training artefacts and
    # have nothing to do with a Silver window.
    Table(
        "fact_request_forecast",
        "scoring",
        deps=("dim_snowfall_event", "dim_plow_zone"),
        from_artefacts=True,
        extra_gates=(
            (
                # a2, stated per version rather than as a total, because the
                # total is `1,298 x however many models have been trained` and
                # so cannot be a constant. This shape catches the failure the
                # total would hide: one version loaded short.
                f"every model_version holds exactly {PREDICTION_CELLS:,} rows (gate a2)",
                "SELECT COUNT(*) FROM (SELECT model_version, COUNT(*) AS n"
                " FROM fact_request_forecast GROUP BY model_version)"
                f" WHERE n <> {PREDICTION_CELLS}",
                0,
            ),
            (
                # The panel is the scheduling-era subset and nothing else. A
                # version trained against a drifted dim_snowfall_event would
                # still be 1,298 rows — just 1,298 of the wrong cells.
                "every forecast row names a scheduling-era event (design §4.1)",
                "SELECT COUNT(*) FROM fact_request_forecast f"
                " LEFT JOIN dim_snowfall_event d"
                " ON d.snowfall_event_id = f.snowfall_event_id"
                " AND d.is_scheduling_era = true"
                " WHERE d.snowfall_event_id IS NULL",
                0,
            ),
            (
                # a3 in its machine-checkable half. The artefact's panel keeps
                # only cells with prior history, so a null baseline here means
                # the trainer's causal expanding mean disagrees with what it
                # filtered on — not "the earliest season", which is already
                # dropped upstream. The prose half (are the nulls explicable?)
                # stays a human check in the launch doc.
                "baseline_count is populated on every row the artefact kept (gate a3)",
                "SELECT COUNT(*) FROM fact_request_forecast WHERE baseline_count IS NULL",
                0,
            ),
        ),
    ),
    Table(
        "fact_winter_event_zone_load",
        "scoring",
        deps=("fact_request_forecast", "dim_plow_zone", "dim_snowfall_event", "fact_event_zone_rank"),
        intelligence=True,
        extra_gates=(
            (
                # b2/b3. 374 = 17 matched plow operations x 22 zones; the other
                # 924 cells are scheduling-era events with no plow operation to
                # draw a rank from. Both are equalities: F6 reads Gold only, so
                # neither number can drift on its own (design §7, L3-b).
                "b2: 374 cells are fully scored (17 plow operations x 22 zones)",
                "SELECT COUNT(*) FROM fact_winter_event_zone_load WHERE score_status = 'scored'",
                374,
            ),
            (
                "b3: 924 cells score on demand + weather only",
                "SELECT COUNT(*) FROM fact_winter_event_zone_load"
                " WHERE score_status = 'partial_no_rank'",
                924,
            ),
            (
                "b4: no_schedule_era is unreachable in H1",
                "SELECT COUNT(*) FROM fact_winter_event_zone_load"
                " WHERE score_status = 'no_schedule_era'",
                0,
            ),
            (
                # b8. The H1 weather factor is a citywide constant repeated
                # across zones; more than one value inside an event would mean
                # the join fanned out, which nothing else would notice.
                "b8: weather factor is constant within each event",
                "SELECT COUNT(*) FROM (SELECT snowfall_event_id"
                " FROM fact_winter_event_zone_load GROUP BY snowfall_event_id"
                " HAVING COUNT(DISTINCT weather_severity_factor) > 1)",
                0,
            ),
            (
                "b9: load_score stays inside [0, 100]",
                "SELECT COUNT(*) FROM fact_winter_event_zone_load"
                " WHERE load_score < 0 OR load_score > 100 OR load_score IS NULL",
                0,
            ),
            (
                # O1's ruling, made executable: partial_no_rank rows carry a
                # score on the 0.70 profile. If someone later "fixes" the SQL to
                # follow the stale load_score comment, this gate fails and sends
                # them to launch §4.2 instead of letting 71.2% of the panel go
                # quietly null.
                "O1: every partial_no_rank row still carries a score (launch §4.2)",
                "SELECT COUNT(*) FROM fact_winter_event_zone_load"
                " WHERE score_status = 'partial_no_rank' AND load_score IS NULL",
                0,
            ),
            (
                "b5: rank_factor is never a fabricated 0",
                "SELECT COUNT(*) FROM fact_winter_event_zone_load WHERE rank_factor = 0",
                0,
            ),
            (
                "one serving version drives the whole panel",
                "SELECT COUNT(DISTINCT forecast_model_version) FROM fact_winter_event_zone_load",
                1,
            ),
        ),
    ),
    Table(
        "fact_recommendation",
        "scoring",
        deps=("fact_winter_event_zone_load", "dim_recommendation_rules"),
        intelligence=True,
        per_model_version=True,
        extra_gates=(
            (
                # b12. A permutation, not just 22 rows: ROW_NUMBER over a
                # partition can only repeat a value if the partition key is
                # wrong, and that is exactly the failure worth catching.
                "b12: rank_model is a permutation of 1..22 within every event",
                "SELECT COUNT(*) FROM (SELECT snowfall_event_id, model_version"
                " FROM fact_recommendation GROUP BY snowfall_event_id, model_version"
                " HAVING COUNT(DISTINCT rank_model) <> 22 OR MAX(rank_model) <> 22"
                " OR MIN(rank_model) <> 1)",
                0,
            ),
            (
                "b12b: rank_baseline is a permutation too",
                "SELECT COUNT(*) FROM (SELECT snowfall_event_id, model_version"
                " FROM fact_recommendation GROUP BY snowfall_event_id, model_version"
                " HAVING COUNT(DISTINCT rank_baseline) <> 22)",
                0,
            ),
            (
                "b11: every attribution_rule_id resolves to a seeded rule",
                "SELECT COUNT(*) FROM fact_recommendation f"
                " LEFT JOIN dim_recommendation_rules d ON d.rule_id = f.attribution_rule_id"
                " WHERE d.rule_id IS NULL",
                0,
            ),
            (
                # Design §6.3: the three no-schedule zones are not in the
                # 22-zone panel, and partial_no_rank cells never reach F7. The
                # rule stays seeded (a seed is data, and another city will need
                # it) but it cannot fire in H1. Asserting 0 keeps it from being
                # investigated as a defect later.
                "RULE-NO-SCHEDULE is unreachable in H1 (design §6.3)",
                "SELECT COUNT(*) FROM fact_recommendation"
                " WHERE attribution_rule_id = 'RULE-NO-SCHEDULE'",
                0,
            ),
            (
                "no attribution_text left a placeholder unsubstituted",
                "SELECT COUNT(*) FROM fact_recommendation WHERE attribution_text LIKE '%{%'",
                0,
            ),
        ),
    ),
)

CHUNKED: dict[str, tuple[int, int]] = {
    # table -> [first year, last year] inclusive, chunked by calendar year.
    "fact_winter_request_daily_by_label": (2008, 2026),
    # Chunked on the *event's* start year, not on the request date: an event
    # belongs to exactly one chunk, so the panel cells are disjoint and no
    # anti-join is needed. The DML's request window deliberately runs past the
    # chunk end to catch an event that crosses New Year.
    "fact_service_request_zone_event": (2008, 2026),
    # Same shape as dim_admin_label below: its grain has no date, but it has to
    # enumerate every `type` value ever observed, which is the same
    # 4,878-partition scan. Overlapping chunks, PK held by the anti-join.
    "dim_service_type": (2008, 2026),
    # Not date-grained, but it has to enumerate label values across the whole
    # history, which is the same 4,878-partition scan. Its chunks overlap, so
    # the SQL anti-joins against the rows earlier chunks already inserted.
    "dim_admin_label": (2008, 2026),
}

BY_NAME = {t.name: t for t in TABLES}


def check_order() -> None:
    """Every dependency must be built before the table that needs it."""
    seen: set[str] = set()
    for table in TABLES:
        missing = [d for d in table.deps if d not in seen]
        if missing:
            raise ValueError(
                f"{table.name} depends on {missing}, which TABLES lists later "
                f"(or not at all). Fix the order in TABLES — it is the build order."
            )
        seen.add(table.name)


def sql_literal(value: str, sql_type: str) -> str:
    """Render one CSV cell as a Trino literal of the DDL's declared type."""
    text = value.strip()
    upper = sql_type.upper()
    if text == "":
        return "NULL"
    if upper.startswith(("VARCHAR", "CHAR")):
        # Doubling the quote is what keeps a neighbourhood called O'Connor from
        # ending the string early.
        return "'" + text.replace("'", "''") + "'"
    if upper.startswith("BOOLEAN"):
        if text.lower() not in {"true", "false"}:
            raise ValueError(f"{text!r} is not a boolean")
        return text.lower()
    if upper.startswith(("INTEGER", "BIGINT", "DOUBLE", "REAL", "DECIMAL")):
        float(text)  # raises if it is not numeric
        return text
    if upper.startswith("DATE"):
        return f"DATE '{text}'"
    if upper.startswith("TIMESTAMP"):
        return f"TIMESTAMP '{text}'"
    raise ValueError(f"no literal rendering for SQL type {sql_type!r}")


def _seed_rows(name: str) -> list[dict[str, str]]:
    with (SEED_DIR / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def seed_placeholders() -> dict[str, str]:
    """Seed facts a DML file needs but cannot read from a Gold table.

    Both are rendered into *string literals* in the SQL, never as a bare
    placeholder in a clause: sqlfluff cannot parse the latter, and an
    unparseable file silently loses the SELECT * and partition-predicate
    checks too (launch doc §7.3).

    `winter_category_order` exists because dim_winter_category's schema is
    frozen with no priority column, and the CSV's row order *is* the multi-hit
    arbitration rule. Row order on a Parquet read is not a guarantee, so the
    order has to travel explicitly rather than be recovered from the table.

    `service_type_keywords` has no Gold table at all — the 17 are frozen — so
    the 9 priority patterns travel as `priority;regex;weight` joined by `|`.
    Neither character occurs in any pattern; the check below keeps it that way.
    """
    order = [r["winter_category"] for r in _seed_rows("winter_category.csv")]
    rows = _seed_rows("service_type_keywords.csv")
    fields = [
        (r["match_priority"].strip(), r["pattern_regex"].strip(), r["priority_weight"].strip())
        for r in rows
    ]
    for parts in (*fields, *((c,) for c in order)):
        for cell in parts:
            if ";" in cell or "|" in cell or "'" in cell or "," in cell:
                raise ValueError(
                    f"{cell!r} contains a delimiter used to encode a seed placeholder "
                    f"(one of ; | , ') — change the encoding, not the seed."
                )
    return {
        "winter_category_order": ",".join(order),
        "service_type_keywords": "|".join(";".join(f) for f in fields),
    }


def seed_select(table: Table, columns: list[Any], run_id: str, built_at: str) -> str:
    """Build `SELECT ... FROM (VALUES ...)` from a seed CSV.

    The CSV's column order must match the DDL's, minus the three lineage
    columns which the build injects. Mismatches raise rather than being
    matched up by name: a tolerant loader turns one misplaced column into a
    silent semantic swap.
    """
    path = SEED_DIR / table.seed
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        raise ValueError(f"{path} is empty")
    header, body = rows[0], rows[1:]
    expected = [c.name for c in columns if c.name not in LINEAGE_COLUMNS]
    if [h.strip() for h in header] != expected:
        raise ValueError(
            f"{path.name} columns {header} do not match {table.name}'s DDL "
            f"columns {expected} (order matters — see build_gold.seed_select)"
        )
    types = {c.name: c.sql_type for c in columns}
    # A seed's upstream is this file, so its lineage date is the file's own
    # last modification — not the build date. source_max_ingest_date answers
    # "how old is the input", and CURRENT_DATE would answer "when did we run",
    # which is what built_at is already for (ADR 0010 D7).
    seed_date = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.UTC).date().isoformat()
    tuples = []
    for line_no, row in enumerate(body, start=2):
        if len(row) != len(expected):
            raise ValueError(
                f"{path.name}:{line_no} has {len(row)} cells, expected {len(expected)}"
            )
        cells = [sql_literal(cell, types[name]) for cell, name in zip(row, expected, strict=True)]
        cells += [f"'{run_id}'", f"TIMESTAMP '{built_at}'", f"DATE '{seed_date}'"]
        tuples.append("(" + ", ".join(cells) + ")")
    all_cols = expected + list(LINEAGE_COLUMNS)
    return (
        "SELECT * FROM (VALUES\n  " + ",\n  ".join(tuples) + "\n) AS t(" + ", ".join(all_cols) + ")"
    )


def artefact_select(
    artefact: Artefact, columns: list[Any], run_id: str, built_at: str
) -> str:
    """Build `SELECT ... FROM (VALUES ...)` from one forecast artefact.

    Same shape as :func:`seed_select`, and deliberately so — an artefact is a
    seed that happens to be produced by a training run instead of typed by a
    person, and F5 is therefore a normal R4 four-step rebuild rather than a
    special case. The only differences are where the rows come from (object
    storage, not config/seeds/) and where `source_max_ingest_date` comes from
    (the artefact's LastModified, not a file mtime).

    One artefact renders one SELECT, so the build issues one INSERT per model
    version. That keeps each VALUES list at 1,298 tuples no matter how many
    versions accumulate, and it means a version that fails to load names
    itself instead of collapsing a single giant statement.
    """
    expected = [c.name for c in columns if c.name not in LINEAGE_COLUMNS]
    if expected != list(PREDICTION_COLUMNS):
        raise ValueError(
            f"fact_request_forecast's DDL columns {expected} no longer match the "
            f"artefact's {list(PREDICTION_COLUMNS)} — the contract is frozen, so "
            f"this is a code change, not a schema change."
        )
    types = {c.name: c.sql_type for c in columns}
    tuples = []
    for row in artefact.rows:
        cells = [sql_literal(cell, types[name]) for cell, name in zip(row, expected, strict=True)]
        cells += [
            f"'{run_id}'",
            f"TIMESTAMP '{built_at}'",
            f"DATE '{artefact.source_date.isoformat()}'",
        ]
        tuples.append("(" + ", ".join(cells) + ")")
    all_cols = expected + list(LINEAGE_COLUMNS)
    return (
        "SELECT * FROM (VALUES\n  " + ",\n  ".join(tuples) + "\n) AS t(" + ", ".join(all_cols) + ")"
    )

# Column names that collide with SQL keywords. Both are real: silver/gold DDL
# declares "type" and "date" quoted, and an unquoted reference parses as the
# keyword and fails at build time, not at review time.
_RESERVED = {"type", "date"}


def _quote(name: str) -> str:
    return f'"{name}"' if name in _RESERVED else name


class Builder:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        # Deliberately lazy: --dry-run must work on a machine that cannot
        # reach Trino at all, which is where the SQL gets reviewed.
        self.settings = None if args.dry_run else load_trino_settings()
        self.bucket = args.bucket
        self.prefix = args.location_prefix
        self.gold_schema = schema_name("gold", self.prefix)
        self.silver_schema = schema_name("silver", self.prefix)
        self.run_id = f"l2-{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%SZ}"
        # One timestamp for the whole batch: 13 tables built in one run must
        # carry one built_at, so `now()` in SQL is not an option.
        self.built_at = f"{dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M:%S.%f}"
        self.conn: Any = None
        # Populated by artefact_selects; the version count in
        # _dynamic_extra_gates is read back off it.
        self._artefacts: list[Artefact] = []

    def connect(self) -> None:
        if self.args.dry_run:
            return
        try:
            self.conn = _connect(self.settings, self.gold_schema)
            self.run("SELECT 1")
        except Exception as exc:  # noqa: BLE001
            print(f"\n{HOST_SHELL_HINT}\n\nunderlying error: {type(exc).__name__}: {exc}")
            raise SystemExit(2) from exc

    def run(self, sql: str) -> list[tuple]:
        cur = self.conn.cursor()
        cur.execute(sql)
        return cur.fetchall()

    def scalar(self, sql: str) -> int:
        return self.run(sql)[0][0]

    # ------------------------------------------------------------------ steps
    def rebuild(self, table: Table) -> None:
        ddl_path = DDL_DIR / f"{table.name}.sql"
        ddl = parse_ddl_file(ddl_path)
        create_sql = render_ddl(ddl_path.read_text(), self.bucket, self.prefix)
        columns = ddl.data_columns
        col_list = ", ".join(_quote(c.name) for c in columns)

        if table.seed:
            selects = [seed_select(table, columns, self.run_id, self.built_at)]
        elif table.from_artefacts:
            selects = self.artefact_selects(table, columns)
        elif table.intelligence:
            selects = self.intelligence_selects(table)
        else:
            selects = self.dml_selects(table)

        if self.args.dry_run:
            print(f"\n===== {table.name} (dry run) =====")
            print(f"-- 1. DROP TABLE IF EXISTS {table.name}")
            print(f"-- 2. purge s3a://{self.bucket}/{self._table_prefix(table)}")
            print(f"-- 3. {create_sql.strip().splitlines()[0]} ...")
            for i, select in enumerate(selects, 1):
                label = f" (chunk {i}/{len(selects)})" if len(selects) > 1 else ""
                print(f"-- 4{label}\nINSERT INTO {table.name} ({col_list})\n{select}\n")
            return

        self.run(f"DROP TABLE IF EXISTS {table.name}")
        purged = _purge_storage(self.bucket, self._table_prefix(table))
        self.run(create_sql)
        stale = self.scalar(f"SELECT COUNT(*) FROM {table.name}")
        if stale:
            raise SystemExit(
                f"{table.name}: {stale} rows visible on a freshly created table — "
                f"the storage purge did not take (purged {purged} objects). "
                f"Building on top of this would union two generations."
            )
        for i, select in enumerate(selects, 1):
            if len(selects) > 1:
                print(f"    chunk {i}/{len(selects)}", flush=True)
            self.run(f"INSERT INTO {table.name} ({col_list})\n{select}")

    @staticmethod
    def _seed_row_gate(table: Table) -> list[Gate]:
        """A seed table must end up with exactly as many rows as its CSV has.

        Derived from the file rather than written down twice: the count is not
        an independent fact about the table, it is a fact about the seed.
        """
        if not table.seed:
            return []
        with (SEED_DIR / table.seed).open(newline="", encoding="utf-8") as handle:
            rows = sum(1 for _ in csv.reader(handle)) - 1
        return [
            Gate(
                source_text=f"COUNT(*) = {rows} (rows in {table.seed})",
                predicate=None,
                expected=rows,
            )
        ]

    def _table_prefix(self, table: Table) -> str:
        parts = [p for p in (self.prefix, "gold", table.name) if p]
        return "/".join(parts)

    def artefact_selects(self, table: Table, columns: list[Any]) -> list[str]:
        """One SELECT per forecast artefact found in object storage.

        🔴 The artefacts are read *before* the DROP in `rebuild`, because they
        are the only copy of the previous versions the table holds. Reading
        them first means a missing or malformed artefact fails while the old
        table is still standing, rather than after it has been dropped and
        purged.
        """
        # Unlike every other table, F5's SELECT cannot be composed offline: its
        # rows live in object storage. On a dry run that cannot reach MinIO,
        # _forecast_versions says so and hands back a placeholder, which has no
        # rows and so renders no VALUES.
        self._forecast_versions()
        if self.args.dry_run and self._artefacts[0].model_version == self.DRY_RUN_VERSION:
            return []
        print(
            f"    {len(self._artefacts)} artefact(s): "
            + ", ".join(a.model_version for a in self._artefacts)
        )
        return [
            artefact_select(a, columns, self.run_id, self.built_at) for a in self._artefacts
        ]

    def serving_version(self) -> str:
        """Which M1 version F6 scores against.

        🔴 F6's grain has no `model_version` and its row count is capped at the
        1,298-cell panel, so exactly one version can drive it — but "the current
        one" is not derivable from the data. `built_at` is identical across
        versions after a rebuild (one timestamp per batch, by design), and
        sorting the version strings picks by prefix: `m1-poisson-nomonth-...`
        sorts after `m1-poisson-...` and would silently win.

        So: one version, use it. More than one, name the one you mean. Guessing
        here would put a deliberately-degraded test model in front of Superset
        without anything raising.
        """
        if self.args.forecast_version:
            available = {a.model_version for a in self._forecast_versions()}
            if self.args.forecast_version not in available:
                raise SystemExit(
                    f"--forecast-version {self.args.forecast_version!r} is not in "
                    f"fact_request_forecast. Available: {', '.join(sorted(available))}"
                )
            return self.args.forecast_version
        versions = [a.model_version for a in self._forecast_versions()]
        if len(versions) > 1:
            raise SystemExit(
                "fact_request_forecast holds more than one model_version and F6 can "
                "only be scored against one:\n  "
                + "\n  ".join(sorted(versions))
                + "\n\nPass --forecast-version <id> (make gold-build "
                "FORECAST_VERSION=<id>). Refusing to pick: version strings do not "
                "sort by recency, and built_at is the build's, not the model's."
            )
        return versions[0]

    # Stands in for a real model_version when --dry-run cannot reach object
    # storage. It makes the printed SQL readable without pretending a version
    # was chosen; a real build never gets here, because load_artefacts raises.
    DRY_RUN_VERSION = "<model_version>"

    def _forecast_versions(self) -> list[Artefact]:
        """The artefacts, loaded once per build and reused by F5/F6/F7."""
        if self._artefacts:
            return self._artefacts
        try:
            self._artefacts = load_artefacts(self.bucket, self.prefix)
        except Exception as exc:  # noqa: BLE001
            if not self.args.dry_run:
                raise
            print(f"    (dry run) cannot read artefacts: {type(exc).__name__}: {exc}")
            self._artefacts = [
                Artefact(
                    model_version=self.DRY_RUN_VERSION,
                    key="",
                    source_date=dt.date.today(),
                    rows=[],
                )
            ]
        return self._artefacts

    def intelligence_selects(self, table: Table) -> list[str]:
        """Read sql/intelligence/<table>.sql — same mechanism as dml_selects.

        F7 is expanded to one statement per model_version, for the same reason
        F5 is expanded per artefact: BO-8 needs a past backtest to stay
        queryable after a retrain, so every version present in F5 is scored.
        Only the demand factor depends on the version; rank and weather come
        off F6 and are version-independent.
        """
        text = self._render_select(INTELLIGENCE_DIR / f"{table.name}.sql")
        if not table.per_model_version:
            return [text.replace("{forecast_version}", self.serving_version())]
        return [
            text.replace("{model_version}", a.model_version)
            for a in self._forecast_versions()
        ]

    def _dynamic_extra_gates(
        self, table: Table
    ) -> tuple[tuple[str, str, int] | tuple[str, str, int, str], ...]:
        """Gates whose expected value is not a property of the schema.

        F5's total row count and version count both depend on how many models
        have been trained, so neither can sit in `extra_gates` as a constant.
        They are still the gates that matter most here: design §5's entire
        claim is that a rebuild does not lose a previous version, and
        "the table holds one full panel per artefact" *is* that claim, made
        executable. Without it a purge that ate three versions and a build that
        only ever had one look identical.
        """
        if table.per_model_version:
            versions = len(self._artefacts)
            return (
                (
                    f"b10: 374 scored cells per model_version ({versions} version(s))",
                    "SELECT COUNT(*) FROM fact_recommendation",
                    versions * 374,
                ),
                (
                    f"every version in F5 was backtested: {versions} distinct model_version",
                    "SELECT COUNT(DISTINCT model_version) FROM fact_recommendation",
                    versions,
                ),
            )
        if not table.from_artefacts:
            return ()
        versions = len(self._artefacts)
        return (
            (
                f"one full panel per artefact: {versions} x {PREDICTION_CELLS:,} rows",
                "SELECT COUNT(*) FROM fact_request_forecast",
                versions * PREDICTION_CELLS,
            ),
            (
                f"no version lost in the rebuild: {versions} distinct model_version",
                "SELECT COUNT(DISTINCT model_version) FROM fact_request_forecast",
                versions,
            ),
        )

    def dml_selects(self, table: Table) -> list[str]:
        """Read sql/dml/<table>.sql and expand it into one SELECT per chunk.

        The file holds a bare SELECT, not a full statement: the INSERT and its
        explicit column list are composed from the DDL so that the columns have
        exactly one source of truth, and so a chunk predicate can be threaded
        in without rewriting the file.
        """
        text = self._render_select(DML_DIR / f"{table.name}.sql")
        if table.name not in CHUNKED:
            return [text]
        # The chunk boundaries are substituted *inside* the date literals the
        # file already writes, rather than replacing a whole predicate. That
        # keeps every DML file parseable SQL — sqlfluff cannot parse a bare
        # {placeholder} in a WHERE clause, and an unlintable file is one nobody
        # checks for SELECT * or a missing partition predicate either.
        first, last = CHUNKED[table.name]
        return [
            text.replace("{chunk_start}", f"{y}-01-01").replace("{chunk_end}", f"{y + 1}-01-01")
            for y in range(first, last + 1)
        ]

    def _render_select(self, path: Path) -> str:
        """Substitute everything a bare SELECT file shares, whichever directory
        it came from (sql/dml/ or sql/intelligence/).

        Not render_ddl(): that one ends by slicing from "CREATE TABLE", which a
        SELECT file has none of. Only the location substitution is shared.
        """
        if not path.exists():
            raise SystemExit(f"{path} does not exist — nothing to build from.")
        text = path.read_text().replace("{bucket}", self.bucket)
        prefix = normalise_prefix(self.prefix)
        if prefix:
            for layer in LAYERS:
                text = text.replace(
                    f"s3a://{self.bucket}/{layer}/", f"s3a://{self.bucket}/{prefix}/{layer}/"
                )
        # Double braces, not single: {silver} sits outside any string literal
        # (a FROM/JOIN schema qualifier), and single-brace text there is not
        # valid SQL — sqlfluff parses it as unparsable rather than as a
        # placeholder. {{ silver }} is real jinja, resolved for lint purposes
        # by the [sqlfluff:templater:jinja:context] entry in .sqlfluff; this
        # is the matching runtime substitution. See .sqlfluff for the rest of
        # the reasoning.
        text = text.replace("{{ silver }}", self.silver_schema)
        for name, value in seed_placeholders().items():
            text = text.replace("{" + name + "}", value)
        text = text.replace("{etl_run_id}", self.run_id).replace("{built_at}", self.built_at)
        return text.strip().rstrip(";")

    def check_gates(self, table: Table) -> list[str]:
        failures = []
        ddl_text = (DDL_DIR / f"{table.name}.sql").read_text()
        gates, notes = parse_gates(ddl_text)
        gates = list(gates) + self._seed_row_gate(table)
        for gate in gates:
            actual = self.scalar(gate.sql(table.name))
            status = "ok " if actual == gate.expected else "FAIL"
            print(f"    [{status}] {gate.source_text}  -> {actual:,}")
            if actual != gate.expected:
                failures.append(f"{table.name}: {gate.source_text} but got {actual:,}")
        for description, sql, expected, *rest in (
            *table.extra_gates,
            *self._dynamic_extra_gates(table),
        ):
            operator = rest[0] if rest else "=="
            actual = self.scalar(sql.format(silver=self.silver_schema))
            passed = actual >= expected if operator == ">=" else actual == expected
            status = "ok " if passed else "FAIL"
            print(f"    [{status}] {description} -> {actual:,} (expected {operator} {expected:,})")
            if not passed:
                failures.append(
                    f"{table.name}: {description} -> {actual:,}, expected {operator} {expected:,}"
                )
        for note in notes:
            print(f"    [note] not machine-checked: {note}")
        return failures

    # ------------------------------------------------------------------- main
    def build(self) -> int:
        check_order()
        selected = [t for t in TABLES if self.args.only in (None, "all", t.stage, t.name)]
        if not selected:
            print(f"--only {self.args.only!r} matched no table or stage.")
            return 2

        print(f"run_id={self.run_id}  built_at={self.built_at}")
        print(f"schema={self.gold_schema}  bucket={self.bucket}  tables={len(selected)}")
        self.connect()
        batch_started = dt.datetime.now(dt.UTC)

        failures: list[str] = []
        try:
            for table in selected:
                print(f"\n--- {table.name} ({table.stage}) ---", flush=True)
                started = dt.datetime.now(dt.UTC)
                self.rebuild(table)
                if self.args.dry_run:
                    continue
                rows = self.scalar(f"SELECT COUNT(*) FROM {table.name}")
                elapsed = (dt.datetime.now(dt.UTC) - started).total_seconds()
                print(f"    {rows:,} rows in {elapsed:.1f}s")
                failures += self.check_gates(table)
        except BaseException as exc:
            # BaseException, not Exception: a long build is also abandoned by
            # Ctrl-C and SIGTERM (an SSH drop), and those are exactly the runs
            # nobody is watching. The notice is best-effort and the original
            # exception always propagates untouched.
            self._notify_outcome(batch_started, f"crashed: {type(exc).__name__}: {exc}")
            raise

        if failures:
            print("\n\n#### GATES FAILED ####")
            for line in failures:
                print(f"  {line}")
            print(
                "\nThe built tables are NOT rolled back. A rebuild is idempotent, "
                "so fix the cause and re-run; do not patch a table by hand."
            )
            self._notify_outcome(batch_started, f"FAILED {len(failures)} gate(s): " + "; ".join(failures))
            return 1
        if not self.args.dry_run:
            print("\nall gates green")
            self._notify_outcome(batch_started, "succeeded, all gates green")
        return 0

    def _notify_outcome(self, batch_started: dt.datetime, outcome: str) -> None:
        """Push the run's outcome to Discord if it ran long enough to walk away from."""
        if self.args.dry_run:
            return
        elapsed = (dt.datetime.now(dt.UTC) - batch_started).total_seconds()
        if elapsed < SLOW_BUILD_THRESHOLD_SECS:
            return
        only = self.args.only or "all"
        notify_build_outcome(
            f"[gold-build] run {self.run_id} (--only {only}) {outcome} — {elapsed:.0f}s",
            elapsed,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", default=None)
    parser.add_argument(
        "--only",
        default=None,
        help="a stage (seeds|dims|facts|scoring|all) or a single table name",
    )
    parser.add_argument(
        "--forecast-version",
        default=None,
        help="Which M1 model_version F6 scores against. Required once "
             "fact_request_forecast holds more than one; see Builder.serving_version.",
    )
    parser.add_argument("--dry-run", action="store_true", help="print the SQL, execute nothing")
    parser.add_argument("--location-prefix", default="", help="smoke namespace, as in apply_ddl")
    return parser


def main(argv: list[str] | None = None) -> int:
    load_cli_env()
    args = build_parser().parse_args(argv)
    if not args.bucket:
        import os

        args.bucket = os.environ.get("S3_BUCKET_NAME")
    if not args.bucket:
        print("Missing bucket: pass --bucket or set S3_BUCKET_NAME.", file=sys.stderr)
        return 2
    try:
        return Builder(args).build()
    except TrinoConfigError as exc:
        print(f"{exc}\n\n{HOST_SHELL_HINT}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
