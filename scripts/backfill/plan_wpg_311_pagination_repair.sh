#!/usr/bin/env bash
# Re-pull the 55 Bronze days corrupted by unordered Socrata pagination.
#
# Incident and evidence:
#   docs/dev/postmortem/bronze-socrata-pagination-incident.md
#
# Until 2026-08-18 the windowed Socrata fetcher paged without `$order=:id`, so a
# day spanning more than one page could repeat *and* drop rows at a page
# boundary. The two defects cancel out in the row count, which is why neither
# check finds all of it and this list is the **union** of both:
#
#   34 days  duplicate keys found by scripts/profiling/bronze_duplicate_scan.py
#   23 days  short against the upstream, per bronze_rowcount_reconcile.py
#   -2 days  in both
#   = 55 days
#
# Re-pulling **overwrites** these Bronze objects. That is a deliberate, approved
# exception to the immutability rule (CLAUDE.md "Never overwrite a Bronze file"),
# taken because the goal is for Bronze to match the upstream and the pre-repair
# state is preserved in the postmortem rather than in object storage. The rule
# itself is unchanged: it exists to stop *today's* data landing on *yesterday's*
# partition, which is not what this does.
#
# Each window is a single day (--end is exclusive). Serial, checkpointed by
# _plan_lib.sh — an interrupted run resumes by re-running the same command.
#
# Verify afterwards with BOTH probes; either one alone is blind to half of it:
#   python -m scripts.profiling.bronze_duplicate_scan --bucket uoip \
#       --source SRC-WPG-311 --dataset service_requests \
#       --key case_id --key interaction_id
#   python -m scripts.profiling.bronze_rowcount_reconcile --bucket uoip \
#       --source SRC-WPG-311 --dataset service_requests
#
# Usage:
#   S3_BUCKET_NAME=uoip SOCRATA_APP_TOKEN=... ./scripts/backfill/plan_wpg_311_pagination_repair.sh
#   DRY_RUN=1 ./scripts/backfill/plan_wpg_311_pagination_repair.sh
#
# Run with dag_audit_bronze paused, as with any other plan script.

source "$(dirname "${BASH_SOURCE[0]}")/_plan_lib.sh"

SOURCE_ID="SRC-WPG-311"

# The affected days, in order. Kept as a literal list on purpose: it is the
# frozen finding of one incident, not something to re-derive at run time — a
# re-derivation after the fix would return an empty list and silently repair
# nothing.
REPAIR_DAYS=(
    2019-06-07 2019-07-25 2019-07-31 2020-06-25 2020-07-31
    2022-04-07 2022-09-02 2023-04-10 2023-06-05 2025-02-24
    2025-07-04 2025-08-06 2025-08-13 2025-08-28 2025-09-02
    2025-09-04 2025-09-08 2025-09-11 2025-09-15 2025-09-17
    2025-10-01 2025-10-10 2025-11-13 2025-12-23 2026-01-06
    2026-01-21 2026-02-08 2026-02-25 2026-03-01 2026-03-09
    2026-04-06 2026-04-15 2026-04-20 2026-04-21 2026-04-22
    2026-04-27 2026-05-08 2026-05-16 2026-06-04 2026-06-06
    2026-06-09 2026-06-10 2026-06-11 2026-06-17 2026-07-03
    2026-07-09 2026-07-10 2026-07-11 2026-07-21 2026-07-26
    2026-07-29 2026-08-02 2026-08-03 2026-08-05 2026-08-07
)

# --end is exclusive, so each window is [day, day+1). Computed in bash rather
# than by shelling out: PYTHON may legitimately be a multi-word command
# ("uv run python") — quoting it as one word makes it "command not found", and
# the empty result then surfaced one layer down as `--end ''`, which is a much
# harder error to read than the one that caused it. Same class of bug as
# 8a0deca; a plan script should not need an interpreter to add one day.
next_day() {
    local y="${1%%-*}" rest="${1#*-}"
    local m="${rest%%-*}" d="${rest#*-}"
    # Strip leading zeros so bash does not read them as octal.
    y=$((10#${y})); m=$((10#${m})); d=$((10#${d}))

    local days_in_month=(0 31 28 31 30 31 30 31 31 30 31 30 31)
    if (( m == 2 )) && (( (y % 4 == 0 && y % 100 != 0) || y % 400 == 0 )); then
        days_in_month[2]=29
    fi

    d=$((d + 1))
    if (( d > days_in_month[m] )); then
        d=1; m=$((m + 1))
        if (( m > 12 )); then m=1; y=$((y + 1)); fi
    fi
    printf '%04d-%02d-%02d\n' "${y}" "${m}" "${d}"
}

plan_start

for day in "${REPAIR_DAYS[@]}"; do
    run_window "${SOURCE_ID}" "${day}" "$(next_day "${day}")" "repair ${day}"
done

plan_done
