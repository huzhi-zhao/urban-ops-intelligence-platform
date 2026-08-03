#!/usr/bin/env bash
# One-time full historical backfill of every backfillable source into Bronze.
#
# Runs the three phases in increasing cost order, so a misconfiguration surfaces
# in seconds rather than after the multi-hour phase:
#
#   ① static reference tables  ×3   seconds     whole-table pull, no window
#   ② weather archive               ~minutes    2008 → today, chunked by year
#   ③ service requests (311)        ~hours      delegates to plan_wpg_311_backfill.sh
#
# NOT included, on purpose: SRC-WPG-SNOW and SRC-Open-Meteo's weather_forecast
# dataset. Both are partition_strategy=snapshot — the upstream overwrites in
# place and keeps no history, so there is nothing behind today to backfill. They
# are collected on the storage node by ingestion/snapshot/, which has its own
# alerting and dead-man switch. Running the CLI against them here would write
# today's ingest_date= partition over a collection that node already took.
#
# ── Resuming and alerting ─────────────────────────────────────────────────────
# Both come from scripts/backfill/_plan_lib.sh; read its header for the details.
# In short:
#
#   * every completed window is appended to var/backfill/*.state, and a re-run
#     skips what is already recorded — so an interrupted run is resumed by
#     re-running the exact same command, with no re-fetching of finished
#     windows. SKIP_STATIC / SKIP_WEATHER / SKIP_311 still exist for skipping a
#     phase deliberately rather than because it finished.
#   * a failed window, a Ctrl-C, a SIGTERM or a dropped SSH session posts to the
#     Discord webhook in SNAPSHOT_ALERT_WEBHOOK_URL (override with
#     BACKFILL_ALERT_WEBHOOK_URL) and names the window it died on.
#   * set BACKFILL_WATCHDOG_URL to a *second* healthchecks.io check to also be
#     told when the machine itself disappears mid-run. Do not reuse
#     SNAPSHOT_WATCHDOG_URL — that check belongs to the daily snapshot
#     collection and a check-in from here would mask its alert.
#
# Pause dag_audit_bronze before starting (launch doc §5): it scans a rolling
# window and calls bulk.py itself, and would compete for the same Socrata token.
#
# Usage:
#   PYTHON="uv run python" S3_BUCKET_NAME=uoip ./scripts/backfill/plan_full_backfill.sh
#   DRY_RUN=1 ./scripts/backfill/plan_full_backfill.sh      # fetch only, no writes
#   SKIP_311=1 ./scripts/backfill/plan_full_backfill.sh     # phases ① ② only
#
# Run it under nohup or tmux — several hours of foreground shell is exactly the
# thing an SSH drop takes down.

source "$(dirname "${BASH_SOURCE[0]}")/_plan_lib.sh"

WEATHER_FROM_YEAR=2008            # BO-3 event segmentation / M1 training reach
TODAY="$(date +%F)"               # --end is exclusive → backfills through yesterday

STATIC_SOURCES=(
    SRC-WPG-PLOW-ZONE
    SRC-WPG-PLOW-SHIFT
    SRC-WPG-PARKING-BAN
)

plan_start

# ── ① static reference tables ────────────────────────────────────────────────
# --start/--end are accepted for CLI uniformity and ignored: a static source has
# no time dimension, it writes one fixed data_static.ndjson.gz. They are passed
# as a fixed sentinel rather than as today's date so the checkpoint key stays
# stable — with $TODAY in the key, a resume the next morning would re-pull all
# three tables for nothing.
if [[ "${SKIP_STATIC:-0}" != "1" ]]; then
    for source_id in "${STATIC_SOURCES[@]}"; do
        run_window "${source_id}" "1970-01-01" "1970-01-02" "static"
    done
fi

# ── ② weather archive ────────────────────────────────────────────────────────
# Open-Meteo is a wide-fetch source: one API call covers the whole window
# regardless of its length. Chunked by year anyway, matching
# dag_backfill_weather_archive's _ARCHIVE_CHUNK_DAYS=365 — not an API limit, but
# it bounds how much a single failed call costs and keeps one response from
# materialising ~6,600 days at once. The chunking is also what gives the
# checkpoint its granularity: a resume re-runs one year, not eighteen.
if [[ "${SKIP_WEATHER:-0}" != "1" ]]; then
    for year in $(seq "${WEATHER_FROM_YEAR}" "$(( ${TODAY%%-*} - 1 ))"); do
        run_window SRC-Open-Meteo "${year}-01-01" "$((year + 1))-01-01" "weather ${year}"
    done
    # The current year's window ends today, so its key changes daily and a
    # resume tomorrow re-runs it. That is correct rather than wasteful: the
    # extra day is data the earlier run could not have fetched. One wide call.
    run_window SRC-Open-Meteo "${TODAY%%-*}-01-01" "${TODAY}" "weather ${TODAY%%-*} (partial)"
fi

# ── ③ service requests ───────────────────────────────────────────────────────
# Delegated rather than inlined: the agreed 311 window set (ten full seasons +
# winters-only before 2016-08) is a scoping decision documented at its own call
# site. Duplicating those windows here would create a second place to drift.
# The child sources the same library, so it keeps its own checkpoint file and
# reports its own failing window; the marker in BACKFILL_NOTIFY_MARKER (exported
# above) keeps that from becoming two alerts for one failure.
if [[ "${SKIP_311:-0}" != "1" ]]; then
    CURRENT_WINDOW="311 (delegated to plan_wpg_311_backfill.sh)"
    echo "=== ${CURRENT_WINDOW} — expect several hours ==="
    "$(dirname "$0")/plan_wpg_311_backfill.sh"
fi

plan_done
