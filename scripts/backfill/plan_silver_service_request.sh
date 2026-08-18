#!/usr/bin/env bash
# One-time historical Bronze -> Silver backfill of SRC-WPG-311 service requests.
#
# The windows are **copied from the Bronze side** (plan_wpg_311_backfill.sh) on
# purpose, not to save thinking:
#
#   2016-08-01 .. today       full, every day        ~3,650 days
#   2008-11-01 .. 2016-03-31  winters only (Nov–Mar)  ~1,210 days
#
# Whatever Bronze is missing, Silver fails in the *same* window, so
# reconciliation between the two layers only ever compares one window name. A
# different slicing here would make "which Bronze window does this Silver
# failure correspond to" an arithmetic problem during an incident.
#
# Each window is one spark-submit. Silver writes overwrite whole date
# partitions (C6/C7), so re-running a window is idempotent and nothing here can
# be corrupted by running it twice.
#
# ── Resuming and alerting ─────────────────────────────────────────────────────
# From scripts/backfill/_plan_lib.sh — read its header. Completed windows go in
# var/backfill/plan_silver_service_request.state and are skipped on re-run, so
# an interrupted run resumes by re-running the same command. Failures and
# interruptions POST to BACKFILL_ALERT_WEBHOOK_URL (falling back to
# SNAPSHOT_ALERT_WEBHOOK_URL) naming the window it stopped at.
#
# The checkpoint's unit is a window, which is why the ten-year leg is sliced by
# calendar year: as one window a failure in year nine would recompute nine
# years on resume. **The slice size is the resume granularity.**
#
# ── Serial by decision ────────────────────────────────────────────────────────
# One window at a time (design §6 O3). The compute node has 7 GB shared with
# Trino and the Hive Metastore; the gain from concurrency is unmeasured and an
# OOM costs hours of already-completed work.
#
# ── Environment ───────────────────────────────────────────────────────────────
# Run from the repo root on the compute node. The prerequisites are the ones
# the job itself needs:
#
#   S3_BUCKET_NAME       target bucket
#   S3_ENDPOINT_URL      MinIO endpoint, passed to s3a below
#   SPARK_MASTER         defaults to spark://localhost:7077
#   SPARK_SUBMIT         defaults to `spark-submit`; override to route through
#                        `docker exec <container> spark-submit` — and then also
#                        set SPARK_JOB_PATH / SPARK_EXECUTOR_PYTHONPATH to the
#                        in-container paths, since the defaults are host paths
#
# ⚠️ S3 credentials are NOT passed here. They are injected as
# AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY on the spark-worker container —
# a --conf would publish them to the Spark UI, the process list and every log.
#
# Usage:
#   S3_BUCKET_NAME=uoip ./scripts/backfill/plan_silver_service_request.sh
#   DRY_RUN=1 ./scripts/backfill/plan_silver_service_request.sh   # print only

source "$(dirname "${BASH_SOURCE[0]}")/_plan_lib.sh"

JOB_ID="SILVER-SRC-WPG-311"      # checkpoint key prefix; not a source registry id

# Both paths are resolved by whatever runs spark-submit, which is not
# necessarily this shell: with SPARK_SUBMIT pointed at `docker exec <c>
# spark-submit`, they must be the paths *inside* that container
# (/opt/airflow/plugins/spark/jobs/... and /opt/airflow/plugins), not here.
# Defaults suit a submit running on the host from the repo root.
JOB="${SPARK_JOB_PATH:-${REPO_ROOT}/spark/jobs/etl_service_request.py}"
EXECUTOR_PYTHONPATH="${SPARK_EXECUTOR_PYTHONPATH:-${REPO_ROOT}}"

FULL_FROM="2016-08-01"           # start of the ten complete seasons
WINTER_FIRST_YEAR=2008           # 311 history starts 2008-06-17
WINTER_LAST_YEAR=2015            # last winter before FULL_FROM

BUCKET="${S3_BUCKET_NAME:-}"
if [[ -z "${BUCKET}" ]]; then
    echo "S3_BUCKET_NAME is not set — refusing to guess the target bucket." >&2
    exit 64
fi

SPARK_SUBMIT="${SPARK_SUBMIT:-spark-submit}"

# SPARK_SUBMIT is documented as accepting a multi-word command — the compute
# node routes it through `docker exec <container> spark-submit`. Word-split it
# into an array once here: expanded as "${SPARK_SUBMIT}" the whole string is
# one command name and the run dies with exit 127 before Spark starts.
read -ra SPARK_SUBMIT_CMD <<< "${SPARK_SUBMIT}"
SPARK_MASTER="${SPARK_MASTER:-spark://localhost:7077}"

# Neither dags/_spark_common.py nor this script set these before — the job ran
# on Spark standalone's bare default (executor-memory=1g). The compute node
# has ~8 GB available (`free -g`, 2026-08-17) shared with Trino/Hive
# Metastore, and this job shuffles 18.4 M rows through zone_assignment's
# spatial UDF: 1g is the same shape of failure as the E0 --emit-events run
# that terminated mid-job with a suspected but unconfirmed OOM. 4g leaves
# headroom for the platform-level services; override via env if C1's timing
# says otherwise.
SPARK_EXECUTOR_MEMORY="${SPARK_EXECUTOR_MEMORY:-4g}"
SPARK_DRIVER_MEMORY="${SPARK_DRIVER_MEMORY:-1g}"

# Kept in step with dags/_spark_common.py, which is the authoritative copy — the
# DAGs and this script must submit the same job the same way.
HADOOP_AWS_VERSION="3.3.4"       # must equal the Hadoop version bundled in Spark 3.5.1
AWS_SDK_VERSION="1.12.262"       # the version hadoop-aws 3.3.4 was compiled against
S3A_JARS="https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/${HADOOP_AWS_VERSION}/hadoop-aws-${HADOOP_AWS_VERSION}.jar,https://repo1.maven.org/maven2/com/amazonaws/aws-java-sdk-bundle/${AWS_SDK_VERSION}/aws-java-sdk-bundle-${AWS_SDK_VERSION}.jar"

# The endpoint is resolved *here*, in this shell, and the failure it prevents
# cost the most time of anything in the E0/E1 run (launch 20260817 §2): writing
# `--conf ...endpoint=$S3_ENDPOINT_URL` inside a quoted submit line expanded to
# an empty string, s3a fell back to s3.amazonaws.com, and the resulting AWS 403
# read exactly like a wrong key.
S3_ENDPOINT="${S3_ENDPOINT_URL:-}"
[[ -n "${S3_ENDPOINT}" ]] || S3_ENDPOINT="$(_env_lookup S3_ENDPOINT_URL)"
if [[ -z "${S3_ENDPOINT}" ]]; then
    echo "S3_ENDPOINT_URL is empty. s3a would silently fall back to" \
         "s3.amazonaws.com and fail with a 403 that looks like a bad key." >&2
    exit 64
fi

run_silver_window() {
    local _job_id="$1" start="$2" end="$3"

    if [[ "${DRY_RUN}" == "1" ]]; then
        echo "--- DRY RUN: ${SPARK_SUBMIT} ... ${JOB} --bucket ${BUCKET}" \
             "--start ${start} --end ${end}"
        return 0
    fi

    "${SPARK_SUBMIT_CMD[@]}" \
        --master "${SPARK_MASTER}" \
        --executor-memory "${SPARK_EXECUTOR_MEMORY}" \
        --driver-memory "${SPARK_DRIVER_MEMORY}" \
        --jars "${S3A_JARS}" \
        --conf "spark.hadoop.fs.s3a.impl=org.apache.hadoop.fs.s3a.S3AFileSystem" \
        --conf "spark.hadoop.fs.s3a.endpoint=${S3_ENDPOINT}" \
        --conf "spark.hadoop.fs.s3a.path.style.access=true" \
        --conf "spark.hadoop.fs.s3a.connection.ssl.enabled=false" \
        --conf "spark.hadoop.fs.s3a.signing-algorithm=AWSS3V4SignerType" \
        --conf "spark.hadoop.mapreduce.fileoutputcommitter.algorithm.version=2" \
        --conf "spark.pyspark.python=/usr/local/bin/python3.11" \
        --conf "spark.pyspark.driver.python=python3" \
        --conf "spark.executorEnv.PYTHONPATH=${EXECUTOR_PYTHONPATH}" \
        "${JOB}" \
        --bucket "${BUCKET}" \
        --start "${start}" \
        --end "${end}" || return $?

    # Spark writes this window's partitions straight to s3a, bypassing Hive
    # Metastore — Trino/Superset report 0 rows for them until this runs
    # (found during L1's single-season gate; see
    # docs/dev/launch/20260817-silver-etl-runnable-launch.md §3.1/§5).
    # A sync failure fails the window: an unsynced window is not usable data.
    "${PYTHON}" -m scripts.ddl.sync_partitions \
        --schema uoip_silver --table silver_service_request
}

WINDOW_RUNNER=run_silver_window

# --end is exclusive, so today's date builds through yesterday; today itself is
# the incremental DAG's job. `date +%F` is the one format GNU and BSD agree on.
TODAY="$(date +%F)"
THIS_YEAR="${TODAY%%-*}"

plan_start

# Winters first: the smaller half, and they exercise the whole path — a
# misconfiguration surfaces in minutes rather than after the long window.
for year in $(seq "${WINTER_FIRST_YEAR}" "${WINTER_LAST_YEAR}"); do
    run_window "${JOB_ID}" "${year}-11-01" "$((year + 1))-04-01" \
        "winter ${year}-$((year + 1))"
done

# Full history, one calendar year per window. The first window starts at
# FULL_FROM (mid-2016) and the last ends today, so both ends are partial by
# construction.
for year in $(seq "${FULL_FROM%%-*}" "${THIS_YEAR}"); do
    start="${year}-01-01"
    end="$((year + 1))-01-01"
    [[ "${start}" < "${FULL_FROM}" ]] && start="${FULL_FROM}"
    [[ "${end}" > "${TODAY}" ]] && end="${TODAY}"
    # The current year's window ends today, so its key changes daily and a
    # resume tomorrow re-runs it — that extra day is data the earlier run could
    # not have processed.
    [[ "${start}" < "${end}" ]] || continue
    run_window "${JOB_ID}" "${start}" "${end}" "full history ${year}"
done

plan_done
