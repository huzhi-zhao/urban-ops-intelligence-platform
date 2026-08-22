"""Load M1's forecast artefacts so `fact_request_forecast` can be rebuilt from them.

Design §5, decision A. F5 carries a conflict the L2 rebuild rule (R4) does not
cover:

- R4 says a Gold table is rebuilt whole: DROP -> purge prefix -> CREATE -> INSERT.
- F5's contract says `old versions are never overwritten` — BO-8 needs a
  backtest run months ago to still be queryable after a retrain.

Running R4 over F5 as-is would purge every previous version's rows, and the
row-count gate — which only ever looks at the current version — would stay
green while it happened.

The resolution is that **the table is not the record**. `train_m1.py` writes
one immutable artefact per model version under `gold/_forecast_runs/`, and F5
is rebuilt *from the set of artefacts*. R4 still applies unchanged; what gets
purged is `gold/fact_request_forecast/`, which the artefacts do not live under.
Losing the table is therefore recoverable and losing a version is not possible
short of deleting an artefact by hand.

This module holds no pandas: `build_gold` runs in the plain project
environment, and pandas is in the `ml` extra. The artefact is a CSV precisely
so that the writer needs pandas and the reader does not.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from dataclasses import dataclass
from typing import Any

# Duplicated from scripts/models/train_m1.py rather than imported: importing it
# would drag pandas/statsmodels into the Gold build, which runs in an
# environment that deliberately does not have them. The two are pinned together
# by test_forecast_artefact_constants_match_the_trainer in the ml suite, which
# does have pandas and can import both.
ARTEFACT_ROOT = "gold/_forecast_runs"
PREDICTIONS_FILE = "predictions.csv"

# The artefact's own columns, in the order F5's DDL declares them. The three
# lineage columns are appended by the build, exactly as for a seed table.
PREDICTION_COLUMNS = (
    "snowfall_event_id",
    "plow_zone",
    "model_version",
    "predicted_count",
    "baseline_count",
    "actual_count",
)


class ArtefactError(RuntimeError):
    """An artefact is missing, malformed, or disagrees with its own directory."""


@dataclass(frozen=True)
class Artefact:
    """One training run's prediction panel, as read back out of object storage."""

    model_version: str
    key: str
    source_date: dt.date
    rows: list[list[str]]


def artefact_root(location_prefix: str = "") -> str:
    """`gold/_forecast_runs`, or its smoke-namespaced twin.

    Uses the same prefix argument as apply_ddl/build_gold so a smoke build
    reads smoke artefacts and can never rebuild the production table from
    them — or, worse, the reverse.
    """
    parts = [p for p in (location_prefix.strip("/"), ARTEFACT_ROOT) if p]
    return "/".join(parts)


def load_artefacts(bucket: str, location_prefix: str = "", client: Any = None) -> list[Artefact]:
    """Every prediction artefact under the root, oldest version first.

    Ordering is by `model_version`, which begins with the training date, so the
    generated INSERTs run in a stable order and two builds over the same
    artefacts produce byte-identical SQL. That is what makes gate a6
    (`same model_version, same rows`) mean anything.
    """
    if client is None:  # pragma: no cover - exercised against real MinIO
        from ingestion.loaders.s3_client import build_s3_client

        client = build_s3_client()

    root = artefact_root(location_prefix)
    prefix = f"{root}/"
    found: list[Artefact] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(f"/{PREDICTIONS_FILE}"):
                continue
            version = key[len(prefix) :].rsplit("/", 1)[0]
            if "/" in version:
                raise ArtefactError(
                    f"s3://{bucket}/{key} is nested deeper than "
                    f"{root}/<model_version>/{PREDICTIONS_FILE} — refusing to guess "
                    f"which part is the version."
                )
            body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
            found.append(
                Artefact(
                    model_version=version,
                    key=key,
                    # The artefact's own age, not the build's. Same reasoning as
                    # a seed CSV's mtime (ADR 0010 D7): source_max_ingest_date
                    # answers "how old is the input", built_at answers "when did
                    # we run".
                    source_date=_as_date(obj["LastModified"]),
                    rows=parse_predictions(body, version, key),
                )
            )

    if not found:
        raise ArtefactError(
            f"no {PREDICTIONS_FILE} under s3://{bucket}/{prefix} — nothing to build "
            f"fact_request_forecast from. Train a model first:\n"
            f"    UV_PROJECT_ENVIRONMENT=.venv-ml uv run --extra ml python -m "
            f"scripts.models.train_m1 --panel-file var/m1-panel.parquet --upload"
        )
    return sorted(found, key=lambda a: a.model_version)


def _as_date(last_modified: Any) -> dt.date:
    if isinstance(last_modified, dt.datetime):
        return last_modified.astimezone(dt.UTC).date()
    return dt.date.fromisoformat(str(last_modified)[:10])


def parse_predictions(body: bytes, model_version: str, key: str) -> list[list[str]]:
    """Read one predictions.csv, checking it is the panel it claims to be.

    Two checks that look like paranoia and are not:

    * the header must match `PREDICTION_COLUMNS` exactly, in order — matching
      up columns by name would turn one transposed pair into a silent semantic
      swap, the same failure `seed_select` refuses;
    * every row's `model_version` cell must equal the directory it was found
      under, because the directory is what the loader trusts for grouping and
      an artefact edited by hand could otherwise smuggle rows into a version
      that was never trained.
    """
    rows = list(csv.reader(io.StringIO(body.decode("utf-8"))))
    if not rows:
        raise ArtefactError(f"s3://.../{key} is empty")
    header = [h.strip() for h in rows[0]]
    if header != list(PREDICTION_COLUMNS):
        raise ArtefactError(
            f"{key}: columns {header} do not match F5's artefact columns "
            f"{list(PREDICTION_COLUMNS)} (order matters)"
        )
    version_at = PREDICTION_COLUMNS.index("model_version")
    body_rows: list[list[str]] = []
    for line_no, row in enumerate(rows[1:], start=2):
        if len(row) != len(PREDICTION_COLUMNS):
            raise ArtefactError(
                f"{key}:{line_no} has {len(row)} cells, expected {len(PREDICTION_COLUMNS)}"
            )
        if row[version_at].strip() != model_version:
            raise ArtefactError(
                f"{key}:{line_no} says model_version={row[version_at].strip()!r} but sits "
                f"under {model_version!r} — one of the two is wrong; the loader will not pick."
            )
        body_rows.append([cell.strip() for cell in row])
    if not body_rows:
        raise ArtefactError(f"{key} has a header but no rows")
    return body_rows
