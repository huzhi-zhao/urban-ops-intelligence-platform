#!/usr/bin/env bash
# Shared machinery for the long-running backfill plan scripts
# (plan_full_backfill.sh, plan_wpg_311_backfill.sh). Source it, do not run it.
#
# It provides the three things a multi-hour unattended run needs and a bare
# `for` loop does not:
#
#   run_window()   one CLI window, skipped if a previous run already finished it
#   checkpointing  an append-only state file, one line per completed window
#   notification   Discord webhook on failure/interruption, watchdog on success
#
# ── Checkpointing ─────────────────────────────────────────────────────────────
# A window is recorded as done only after `scripts.backfill.main` exits 0 for
# it. Re-running the script therefore resumes at the first window that never
# completed. This is *coarse* resumption: the unit is a whole window, so a
# window that died at day 300 of 365 re-fetches all 365 days. Bronze writes are
# idempotent (same day → same object path → overwrite), so that costs time and
# API quota, never correctness. It is also why the long 311 history is sliced by
# year rather than issued as one 10-year window — see plan_wpg_311_backfill.sh.
#
# The state file is untracked (var/ is gitignored) and safe to delete: deleting
# it turns the next run back into a full re-backfill.
#
# ⚠️ The state file records *that a window was requested and the CLI exited 0*.
# It is not a verification that Bronze holds the data — nothing here reads back
# from MinIO. If you suspect a window landed wrong, delete its line (or the
# whole file) and re-run; do not trust the checkpoint over dag_audit_bronze.
#
# DRY_RUN=1 never writes checkpoints. A dry run that marked windows done would
# make the real run silently skip them.
#
# ── Notification ──────────────────────────────────────────────────────────────
# Same two-channel split as snapshot collection (ingestion/snapshot/notify.py),
# for the same reason, and reading the same webhook:
#
#   BACKFILL_ALERT_WEBHOOK_URL, falling back to SNAPSHOT_ALERT_WEBHOOK_URL
#       POSTed when a window fails or the run is interrupted (Ctrl-C, SIGTERM,
#       SSH drop). Covers "the run died while a process was alive to say so".
#
#   BACKFILL_WATCHDOG_URL      (optional, no fallback — see below)
#       healthchecks.io-style dead-man switch: /start when the run begins, a
#       plain ping on success, /fail on failure. Covers "the box went away
#       mid-run", where no process is left to POST anything.
#
# The watchdog deliberately does **not** fall back to SNAPSHOT_WATCHDOG_URL.
# That check is the BO-7 snapshot collection's dead-man switch on a 1-day
# period; pinging it from here would check in on a day the snapshot collection
# never ran and suppress exactly the alert it exists to raise. If you want
# dead-man coverage for a backfill, create a second check and put its URL in
# BACKFILL_WATCHDOG_URL. Unset simply means "failures are still reported, but a
# machine that vanishes mid-run is not".
#
# Both channels are optional and neither can fail the run: an unreachable
# webhook must not be what stops a six-hour backfill.

# Sourced-only guard. ${BASH_SOURCE[0]} == $0 exactly when executed directly.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "_plan_lib.sh is a library; source it from a plan_*.sh script." >&2
    exit 64
fi

# -E propagates the ERR trap into functions and subshells. Without it the trap
# set here would not fire for a failure inside run_window — i.e. for every
# failure this file exists to report.
set -Eeuo pipefail

PLAN_NAME="$(basename "${0}" .sh)"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# PEP 394 only guarantees `python3`. Override for a venv or uv:
#   PYTHON="uv run python" ./scripts/backfill/plan_full_backfill.sh
PYTHON="${PYTHON:-python3}"
export PYTHON                     # a delegated child script reads it too

DRY_RUN="${DRY_RUN:-0}"
EXTRA_ARGS=()
[[ "${DRY_RUN}" == "1" ]] && EXTRA_ARGS+=(--dry-run)

STATE_FILE="${BACKFILL_STATE_FILE:-${REPO_ROOT}/var/backfill/${PLAN_NAME}.state}"
mkdir -p "$(dirname "${STATE_FILE}")"

CURRENT_WINDOW="(none yet)"
WINDOWS_RUN=0
WINDOWS_SKIPPED=0
RUN_STARTED_AT="$(date +%s)"

# ── Reading .env from bash ────────────────────────────────────────────────────
# scripts/_env.py loads .env for the Python CLI, but that happens inside the
# child process — this shell never sees those values. The webhook URLs live in
# .env, so look them up here too. Grep rather than `source`: .env is a config
# file, not a shell script, and sourcing it would execute anything in it.
_env_lookup() {
    local key="$1" line
    [[ -f "${REPO_ROOT}/.env" ]] || return 0
    line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "${REPO_ROOT}/.env" \
            | tail -n 1 || true)"
    [[ -n "${line}" ]] || return 0
    line="${line#*=}"
    # Trailing whitespace and a CRLF's \r survive an unquoted value in the file
    # and would be sent as part of the URL — curl fails on that, and the failure
    # would only ever be seen when something has already gone wrong.
    line="${line%"${line##*[![:space:]]}"}"
    line="${line#"${line%%[![:space:]]*}"}"
    # Strip one layer of surrounding quotes, as python-dotenv does.
    line="${line%\"}"; line="${line#\"}"
    line="${line%\'}"; line="${line#\'}"
    printf '%s' "${line}"
}

# An exported value wins over .env, matching load_cli_env()'s override=False.
ALERT_URL="${BACKFILL_ALERT_WEBHOOK_URL:-${SNAPSHOT_ALERT_WEBHOOK_URL:-}}"
[[ -n "${ALERT_URL}" ]] || ALERT_URL="$(_env_lookup BACKFILL_ALERT_WEBHOOK_URL)"
[[ -n "${ALERT_URL}" ]] || ALERT_URL="$(_env_lookup SNAPSHOT_ALERT_WEBHOOK_URL)"

WATCHDOG_URL="${BACKFILL_WATCHDOG_URL:-}"
[[ -n "${WATCHDOG_URL}" ]] || WATCHDOG_URL="$(_env_lookup BACKFILL_WATCHDOG_URL)"

# ── Notification ──────────────────────────────────────────────────────────────

_json_escape() {
    # Minimal JSON string escaping: backslash, quote, newline. Enough for the
    # messages built here (window labels and exit codes), and it keeps this file
    # free of a jq dependency.
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    printf '%s' "${s}"
}

# When plan_full_backfill.sh delegates to plan_wpg_311_backfill.sh, both shells
# have this trap installed. The child reports the precise window and touches the
# marker; the parent then sees the marker and stays quiet, so one failure
# produces one Discord message rather than two.
if [[ -z "${BACKFILL_NOTIFY_MARKER:-}" ]]; then
    BACKFILL_NOTIFY_MARKER="$(dirname "${STATE_FILE}")/.notified-$$"
    export BACKFILL_NOTIFY_MARKER
    _OWNS_MARKER=1
else
    _OWNS_MARKER=0
fi

_already_reported() { [[ -e "${BACKFILL_NOTIFY_MARKER}" ]]; }

notify_failure() {
    local message="$1"
    # mkdir, not touch: parent and child can hit this in the same instant when
    # one Ctrl-C reaches both, and mkdir is the atomic "claim it" primitive.
    mkdir "${BACKFILL_NOTIFY_MARKER}" 2>/dev/null || return 0

    if [[ -z "${ALERT_URL}" ]]; then
        echo "!!! no alert webhook configured (BACKFILL_ALERT_WEBHOOK_URL /" \
             "SNAPSHOT_ALERT_WEBHOOK_URL) — nobody was told" >&2
        return 0
    fi
    # "content" is Discord's required field; "text" is Slack's. Sending both
    # keeps this working if the webhook is ever repointed — each side ignores
    # the key it does not know.
    local escaped payload
    escaped="$(_json_escape "${message}")"
    payload="$(printf '{"content": "%s", "text": "%s"}' "${escaped}" "${escaped}")"
    curl -fsS -m 10 -X POST -H 'Content-Type: application/json' \
        -d "${payload}" "${ALERT_URL}" >/dev/null \
        || echo "!!! could not deliver the alert to the webhook" >&2
}

watchdog_ping() {
    # $1: "" for success, "/start" or "/fail" for the lifecycle endpoints.
    local suffix="${1:-}"
    [[ -n "${WATCHDOG_URL}" ]] || return 0
    curl -fsS -m 10 "${WATCHDOG_URL}${suffix}" >/dev/null \
        || echo "!!! could not reach the watchdog (${suffix:-ping})" >&2
}

_resume_hint() {
    echo "--- resume with the same command: completed windows are recorded in"
    echo "---   ${STATE_FILE}"
    echo "--- and will be skipped. Delete that file to force a full re-backfill."
}

_on_error() {
    local code="$1"
    # A delegated child that already reported: it named the actual window and
    # sent the alert. Repeating the resume hint here would only bury it.
    if _already_reported; then
        echo "!!! aborting (exit ${code}): the step above already reported it" >&2
        # The alert is the child's, but the watchdog check is this run's.
        [[ "${_OWNS_MARKER}" == "1" ]] && watchdog_ping /fail
        return 0
    fi
    echo "!!! FAILED (exit ${code}) at window: ${CURRENT_WINDOW}" >&2
    notify_failure "[${PLAN_NAME}] backfill FAILED on $(hostname) — exit ${code} at window: ${CURRENT_WINDOW}. Resume by re-running the script; ${WINDOWS_RUN} window(s) completed this run."
    [[ "${_OWNS_MARKER}" == "1" ]] && watchdog_ping /fail
    _resume_hint >&2
}

_on_signal() {
    local signame="$1"
    # Ctrl-C reaches every process in the foreground group, so a delegating run
    # gets it in both shells. Whichever reports first names the real window.
    if _already_reported; then
        echo "!!! INTERRUPTED (${signame}) — reported by the step above" >&2
        [[ "${_OWNS_MARKER}" == "1" ]] && watchdog_ping /fail
    else
        echo "!!! INTERRUPTED (${signame}) at window: ${CURRENT_WINDOW}" >&2
        notify_failure "[${PLAN_NAME}] backfill INTERRUPTED (${signame}) on $(hostname) at window: ${CURRENT_WINDOW}. Resume by re-running the script; ${WINDOWS_RUN} window(s) completed this run."
        [[ "${_OWNS_MARKER}" == "1" ]] && watchdog_ping /fail
        _resume_hint >&2
    fi
    # Re-raise with the default handler so the exit status is a real 128+N and
    # a parent shell (or plan_full_backfill.sh) sees a signal, not a plain exit.
    trap - "${signame}"
    kill -s "${signame}" $$
}

_on_exit() {
    [[ "${_OWNS_MARKER}" == "1" && -d "${BACKFILL_NOTIFY_MARKER}" ]] \
        && rmdir "${BACKFILL_NOTIFY_MARKER}"
    return 0
}

trap '_on_error $?' ERR
trap '_on_signal INT' INT
trap '_on_signal TERM' TERM
trap '_on_signal HUP' HUP
trap '_on_exit' EXIT

# ── Checkpointing ─────────────────────────────────────────────────────────────

state_is_done() {
    [[ -f "${STATE_FILE}" ]] || return 1
    awk -v key="$1" -F'\t' '$1 == key { hit = 1; exit } END { exit !hit }' \
        "${STATE_FILE}"
}

state_mark() {
    [[ "${DRY_RUN}" == "1" ]] && return 0
    printf '%s\t%s\n' "$1" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "${STATE_FILE}"
}

# ── The one thing every plan script calls ─────────────────────────────────────

run_bronze_window() {
    # The default runner: one Bronze ingest window through the backfill CLI.
    local source_id="$1" start="$2" end="$3"
    ${PYTHON} -m scripts.backfill.main \
        --source "${source_id}" \
        --start "${start}" \
        --end "${end}" \
        "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"
}

# Which command a window actually runs. Bronze plans leave this alone; the
# Silver plan points it at a spark-submit wrapper instead. Everything else —
# checkpointing, the alert traps, the watchdog — is layer-agnostic and is the
# whole reason this indirection exists rather than a second copy of the file.
#
# A runner receives (source_or_job_id, start, end) and must exit non-zero on
# failure: the ERR trap, not a return value, is what reports the window.
WINDOW_RUNNER="${WINDOW_RUNNER:-run_bronze_window}"

run_window() {
    local source_id="$1" start="$2" end="$3" label="$4"
    # The key is what the runner is actually asked to do, not the human label,
    # so renaming a label does not orphan its checkpoint. It carries no layer
    # marker because the state file is already per plan script (PLAN_NAME).
    local key="${source_id}|${start}|${end}"

    if state_is_done "${key}"; then
        WINDOWS_SKIPPED=$((WINDOWS_SKIPPED + 1))
        echo "--- skip (already done): ${label}: ${source_id} [${start}, ${end})"
        return 0
    fi

    CURRENT_WINDOW="${label}: ${source_id} [${start}, ${end})"
    echo "=== ${CURRENT_WINDOW} ==="
    "${WINDOW_RUNNER}" "${source_id}" "${start}" "${end}"

    state_mark "${key}"
    WINDOWS_RUN=$((WINDOWS_RUN + 1))
}

plan_start() {
    echo "=== ${PLAN_NAME} starting on $(hostname) ==="
    echo "--- state file: ${STATE_FILE}"
    [[ "${DRY_RUN}" == "1" ]] && echo "--- DRY RUN: no writes, no checkpoints"
    [[ "${_OWNS_MARKER}" == "1" ]] && watchdog_ping /start
    return 0
}

plan_done() {
    local elapsed=$(( $(date +%s) - RUN_STARTED_AT ))
    echo "=== done: ${PLAN_NAME} — ${WINDOWS_RUN} window(s) run," \
         "${WINDOWS_SKIPPED} skipped, ${elapsed}s ==="
    [[ "${_OWNS_MARKER}" == "1" ]] && watchdog_ping
    return 0
}
