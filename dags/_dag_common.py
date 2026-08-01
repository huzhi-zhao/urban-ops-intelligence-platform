"""
Shared defaults for all NYC-UOIP DAGs (backfill and incremental ingest).

Import pattern in every DAG:
    from _dag_common import DEFAULT_ARGS, backfill_params, get_bucket
    from _dag_common import get_yesterday, get_last_month   # incremental DAGs only
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta

from airflow.models.param import Param

logger = logging.getLogger(__name__)

# Ingest DAGs start catching up from this date (first day we deployed incremental ingest).
INGEST_START_DATE = datetime(2026, 6, 16)

DEFAULT_ARGS = {
    "owner": "nyc-uoip",
    "depends_on_past": False,
    "start_date": INGEST_START_DATE,
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
    "email_on_failure": False,
}

# Standard Params for all date-range backfill DAGs.
# In the Airflow UI: Trigger DAG w/ Config → fill these fields.
backfill_params = {
    "start": Param(
        "2024-01-01",
        type="string",
        description="Inclusive start date (YYYY-MM-DD)",
        format="date",
    ),
    "end": Param(
        "2025-01-01",
        type="string",
        description="Exclusive end date (YYYY-MM-DD)",
        format="date",
    ),
    "bucket": Param(
        "",
        type=["string", "null"],
        description="Object-storage bucket name. Empty = use S3_BUCKET_NAME env var.",
    ),
}


def get_yesterday(context: dict) -> date:
    """Return the data date for a daily incremental DAG run.

    Uses data_interval_start so the result is idempotent: re-running the
    same DAG Run always returns the same date regardless of wall-clock time.

    For a schedule of "0 6 * * *" triggered on 2026-06-17:
        data_interval_start = 2026-06-16 06:00 UTC
        → returns date(2026, 6, 16)
    """
    return context["data_interval_start"].date()


def get_last_month(context: dict) -> tuple[date, date]:
    """Return (month_start, month_end) for a monthly incremental DAG run.

    For a schedule of "0 6 1 * *" triggered on 2026-06-01:
        data_interval_start = 2026-05-01 06:00 UTC
        → returns (date(2026, 5, 1), date(2026, 6, 1))
    """
    first_of_interval_month = context["data_interval_start"].date().replace(day=1)
    return first_of_interval_month, first_of_interval_month.replace(
        month=first_of_interval_month.month % 12 + 1,
        year=first_of_interval_month.year + (1 if first_of_interval_month.month == 12 else 0),
    )


def get_bucket(params) -> str:
    """Resolve the bucket from a DAG Param or the S3_BUCKET_NAME env var."""
    bucket = (params.get("bucket") or "").strip()
    if not bucket:
        bucket = os.environ.get("S3_BUCKET_NAME", "").strip()
    if not bucket:
        raise ValueError(
            "Object-storage bucket not set. Pass the 'bucket' Param when triggering "
            "the DAG, or set the S3_BUCKET_NAME environment variable on the "
            "Airflow containers."
        )
    return bucket
