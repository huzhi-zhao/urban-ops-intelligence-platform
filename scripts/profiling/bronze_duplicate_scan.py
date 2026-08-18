"""Scan Bronze shards for duplicate primary keys left by unordered pagination.

Why this exists: ``SocrataFetcher._fetch_window`` paged without ``$order=:id``
until 2026-08-18, so any window wider than one page could repeat *or drop* rows
at a page boundary. The repeats are findable — that is this probe. The drops are
not: nothing in a shard says a row should have been there. So a shard flagged
here needs re-pulling, and a *clean* shard larger than one page is evidence of
nothing in particular.

Reads Bronze straight from object storage; it does not touch Silver or Spark.

Usage:
    python -m scripts.profiling.bronze_duplicate_scan \\
        --bucket uoip --source SRC-WPG-311 --dataset service_requests \\
        --key case_id --key interaction_id > var/bronze-dup-scan.json

Exit codes: 0 = clean, 2 = duplicates found, 1 = the scan failed.
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from ingestion.loaders.s3_client import build_s3_client

logger = logging.getLogger(__name__)

DATA_SUFFIXES = (".ndjson.gz", ".ndjson")
SOCRATA_PAGE_SIZE = 1000  # DEFAULT_PAGE_SIZE in ingestion/clients/socrata_client.py


@dataclass
class ShardReport:
    """One Bronze data object's duplicate accounting."""

    key: str
    total_rows: int
    distinct_keys: int
    duplicate_keys: int
    excess_rows: int
    pages: int
    byte_identical: bool

    @property
    def is_dirty(self) -> bool:
        return self.excess_rows > 0


def _iter_data_blobs(client: Any, bucket: str, prefix: str):
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(DATA_SUFFIXES):
                yield obj["Key"]


def scan_shard(
    client: Any, bucket: str, key: str, key_fields: tuple[str, ...]
) -> ShardReport:
    """Count duplicate ``key_fields`` tuples inside one shard."""
    raw = client.get_object(Bucket=bucket, Key=key)["Body"].read()
    text = gzip.decompress(raw) if key.endswith(".gz") else raw
    lines = text.decode().splitlines()

    counts: Counter[tuple[Any, ...]] = Counter()
    lines_by_key: dict[tuple[Any, ...], list[str]] = {}
    for line in lines:
        rec = json.loads(line)
        k = tuple(rec.get(f) for f in key_fields)
        counts[k] += 1
        lines_by_key.setdefault(k, []).append(line)

    dups = {k: n for k, n in counts.items() if n > 1}
    # Whether the repeats are byte-identical separates "we fetched the same row
    # twice" from "the upstream really holds two rows on this key" — different
    # problems with different fixes, and only the first one is ours to repair.
    byte_identical = all(len(set(lines_by_key[k])) == 1 for k in dups)

    return ShardReport(
        key=key,
        total_rows=len(lines),
        distinct_keys=len(counts),
        duplicate_keys=len(dups),
        excess_rows=sum(n - 1 for n in dups.values()),
        pages=-(-len(lines) // SOCRATA_PAGE_SIZE),
        byte_identical=byte_identical,
    )


def run_scan(
    bucket: str, source_id: str, dataset: str, key_fields: tuple[str, ...]
) -> list[ShardReport]:
    client = build_s3_client()
    prefix = f"bronze/raw/{source_id}/{dataset}/"
    reports: list[ShardReport] = []
    for blob_key in _iter_data_blobs(client, bucket, prefix):
        report = scan_shard(client, bucket, blob_key, key_fields)
        reports.append(report)
        if report.is_dirty:
            logger.warning(
                "DIRTY %s: %d rows, %d duplicate key(s), %d excess row(s), "
                "%d page(s), byte-identical=%s",
                report.key, report.total_rows, report.duplicate_keys,
                report.excess_rows, report.pages, report.byte_identical,
            )
        else:
            logger.info("ok %s: %d rows, %d page(s)",
                        report.key, report.total_rows, report.pages)
    return reports


def summarize(reports: list[ShardReport]) -> dict[str, Any]:
    dirty = [r for r in reports if r.is_dirty]
    multipage = [r for r in reports if r.pages > 1]
    return {
        "shards_scanned": len(reports),
        "shards_multipage": len(multipage),
        "shards_dirty": len(dirty),
        "excess_rows_total": sum(r.excess_rows for r in dirty),
        "dirty_shards_all_byte_identical": all(r.byte_identical for r in dirty),
        # A single-page shard cannot have been corrupted by page ordering, so a
        # duplicate there would mean something else entirely.
        "dirty_single_page_shards": [r.key for r in dirty if r.pages <= 1],
        "dirty_shards": [asdict(r) for r in dirty],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--source", required=True, help="e.g. SRC-WPG-311")
    parser.add_argument("--dataset", required=True, help="e.g. service_requests")
    parser.add_argument(
        "--key", action="append", required=True,
        help="Primary-key field; repeat for a composite key.",
    )
    parser.add_argument("--json", help="Write the full report to this path.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    reports = run_scan(args.bucket, args.source, args.dataset, tuple(args.key))
    summary = summarize(reports)

    print(json.dumps(summary, indent=2))
    if args.json:
        # A failed write must not discard a scan that already succeeded: this
        # walks every shard in the source and takes tens of minutes, and the
        # answer is already on stdout by now. Ending on a traceback would make
        # a complete run look like a wasted one — which is exactly what a
        # PermissionError on a non-mounted container path did on 2026-08-18.
        try:
            with open(args.json, "w") as fh:
                json.dump(
                    {"summary": summary, "shards": [asdict(r) for r in reports]},
                    fh, indent=2,
                )
        except OSError as e:
            logger.warning(
                "Could not write %s (%s). The summary above is complete; "
                "redirect stdout instead.", args.json, e,
            )

    # 2 = dirty shards found, distinct from 1 = the scan itself failed.
    sys.exit(2 if summary["shards_dirty"] else 0)


if __name__ == "__main__":
    main()
