#!/usr/bin/env bash
# One-time historical backfill of SRC-WPG-311 into Bronze.
#
# The agreed scope (docs/dev/launch/city-instance-switchover-launch.md §7.1):
#
#   2016-08-01 .. yesterday   full, every day        ~3,650 days
#   2008-11-01 .. 2016-03-31  winters only (Nov–Mar)  ~1,210 days
#
# The boundary gives exactly ten complete snow seasons (2016-17 … 2025-26) with
# their off-season baselines, so M1 trains on whole seasons. Before it, only the
# winters are collected — which is safe precisely because the sparse region is
# frozen history: dag_audit_bronze scans a rolling 14-day window and never
# reaches back into it, so no "seasonal gap" concept is needed anywhere in the
# pipeline.
#
# Each day is one Socrata query writing one Bronze file. Re-running a window is
# idempotent (same day → same file), so an interrupted run is resumed by simply
# re-running it. A failed slice does not abort the others, but a window that had
# any failure exits non-zero, so the loop below stops rather than reporting
# success on a partial backfill.
#
# Expect several hours in total. Run it with `nohup`/`tmux`, and run it while
# dag_audit_bronze is paused (launch doc §5) so the two do not fetch the same
# partitions concurrently under one Socrata token.
#
# Usage:
#   S3_BUCKET_NAME=uoip SOCRATA_APP_TOKEN=... ./scripts/backfill/plan_wpg_311_backfill.sh
#   DRY_RUN=1 ./scripts/backfill/plan_wpg_311_backfill.sh    # fetch only, no writes

set -euo pipefail

SOURCE_ID="SRC-WPG-311"
FULL_FROM="2016-08-01"          # start of the ten complete seasons
WINTER_FIRST_YEAR=2008          # 311 history starts 2008-06-17
WINTER_LAST_YEAR=2015           # last winter before FULL_FROM
EXTRA_ARGS=()
[[ "${DRY_RUN:-0}" == "1" ]] && EXTRA_ARGS+=(--dry-run)

# Not bare `python`: PEP 394 only guarantees `python3`, and on a machine where
# `python` is absent this script dies on the very first window after you have
# already walked away from it. Override for a venv or uv:
#   PYTHON="uv run python" ./scripts/backfill/plan_wpg_311_backfill.sh
PYTHON="${PYTHON:-python3}"

run_window() {
    local start="$1" end="$2" label="$3"
    echo "=== ${label}: [${start}, ${end}) ==="
    ${PYTHON} -m scripts.backfill.main \
        --source "${SOURCE_ID}" \
        --start "${start}" \
        --end "${end}" \
        "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"
}

# Winters first: they are the smaller half and exercise the whole path, so a
# configuration mistake surfaces in minutes rather than after the long window.
for year in $(seq "${WINTER_FIRST_YEAR}" "${WINTER_LAST_YEAR}"); do
    run_window "${year}-11-01" "$((year + 1))-04-01" "winter ${year}-$((year + 1))"
done

# `date` is not portable between GNU and BSD; both accept plain `+%F` for today,
# and --end is exclusive, so today's date backfills through yesterday. Today
# itself is the incremental DAG's job.
run_window "${FULL_FROM}" "$(date +%F)" "full history"

echo "=== done: ${SOURCE_ID} Bronze backfill complete ==="
