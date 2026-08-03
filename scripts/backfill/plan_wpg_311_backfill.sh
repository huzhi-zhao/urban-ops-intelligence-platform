#!/usr/bin/env bash
# One-time historical backfill of SRC-WPG-311 into Bronze.
#
# The agreed scope (docs/dev/launch/20260803-city-instance-switchover-launch.md §7.1):
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
# idempotent (same day → same file), so nothing here can be corrupted by running
# it twice.
#
# ── Resuming and alerting ─────────────────────────────────────────────────────
# From scripts/backfill/_plan_lib.sh — read its header. Completed windows are
# recorded in var/backfill/plan_wpg_311_backfill.state and skipped on re-run, so
# an interrupted run resumes by re-running the same command. A failure or an
# interruption posts to the Discord webhook in SNAPSHOT_ALERT_WEBHOOK_URL
# (override: BACKFILL_ALERT_WEBHOOK_URL) naming the window it stopped at.
#
# The checkpoint's unit is a window, so the ~10-year "full history" leg is
# sliced by calendar year: as one window, a failure in year nine would re-fetch
# nine years of days on resume. The union of the yearly windows is exactly the
# window it replaced — this is checkpoint granularity, not a scope change.
#
# Expect several hours in total. Run it with `nohup`/`tmux`, and run it while
# dag_audit_bronze is paused (launch doc §5) so the two do not fetch the same
# partitions concurrently under one Socrata token.
#
# Usage:
#   S3_BUCKET_NAME=uoip SOCRATA_APP_TOKEN=... ./scripts/backfill/plan_wpg_311_backfill.sh
#   DRY_RUN=1 ./scripts/backfill/plan_wpg_311_backfill.sh    # fetch only, no writes

source "$(dirname "${BASH_SOURCE[0]}")/_plan_lib.sh"

SOURCE_ID="SRC-WPG-311"
FULL_FROM="2016-08-01"          # start of the ten complete seasons
WINTER_FIRST_YEAR=2008          # 311 history starts 2008-06-17
WINTER_LAST_YEAR=2015           # last winter before FULL_FROM

# `date` is not portable between GNU and BSD; both accept plain `+%F` for today,
# and --end is exclusive, so today's date backfills through yesterday. Today
# itself is the incremental DAG's job.
TODAY="$(date +%F)"
THIS_YEAR="${TODAY%%-*}"

plan_start

# Winters first: they are the smaller half and exercise the whole path, so a
# configuration mistake surfaces in minutes rather than after the long window.
for year in $(seq "${WINTER_FIRST_YEAR}" "${WINTER_LAST_YEAR}"); do
    run_window "${SOURCE_ID}" "${year}-11-01" "$((year + 1))-04-01" \
        "winter ${year}-$((year + 1))"
done

# Full history, one calendar year per window. The first window starts at
# FULL_FROM (mid-2016) and the last ends today, so the two ends are partial by
# construction.
for year in $(seq "${FULL_FROM%%-*}" "${THIS_YEAR}"); do
    start="${year}-01-01"
    end="$((year + 1))-01-01"
    [[ "${start}" < "${FULL_FROM}" ]] && start="${FULL_FROM}"
    [[ "${end}" > "${TODAY}" ]] && end="${TODAY}"
    # The current year's window ends today, so its key changes daily and a
    # resume tomorrow re-runs it — that extra day is data the earlier run could
    # not have fetched.
    [[ "${start}" < "${end}" ]] || continue
    run_window "${SOURCE_ID}" "${start}" "${end}" "full history ${year}"
done

plan_done
