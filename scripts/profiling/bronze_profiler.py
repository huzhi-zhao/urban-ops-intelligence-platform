"""
Bronze Data Profiler — reads GCS Bronze layer and produces a data quality report.

Usage:
    python -m scripts.profiling.bronze_profiler \\
        --bucket nyc-uoip-prod \\
        [--source SRC-NYC-311] \\
        [--sample-files 3] \\
        [--out-dir reports/]

Steps:
  1. Scan all manifest files for each source → coverage map (dates present,
     record counts per partition).
  2. Download a sample of data files → field-level profiling (null rates,
     timestamp anomalies, borough normalisation, numeric distributions).
  3. Write reports/<source_id>_profile.json + reports/summary.md.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from google.cloud import storage

# ── GCS helpers ───────────────────────────────────────────────────────────────

def _gcs_client() -> storage.Client:
    return storage.Client()


def _list_blobs(bucket: storage.Bucket, prefix: str) -> list[storage.Blob]:
    return list(bucket.list_blobs(prefix=prefix))


def _download_json(blob: storage.Blob) -> Any:
    raw = blob.download_as_bytes()
    return json.loads(raw)


# ── Source layout knowledge ────────────────────────────────────────────────────

SOURCES = {
    "SRC-NYC-311": {
        "datasets": ["nyc_311"],
        "strategy": "daily",
        "timestamp_field": "created_date",
        "borough_field": "borough",
        "numeric_fields": [],
        "expected_daily": 8_000,
    },
    "SRC-NYPD": {
        "datasets": [
            "nypd_collisions",
            "nypd_complaint_historic",
            "nypd_complaint_current",
            "nypd_shooting_incident",
        ],
        "strategy": "monthly",
        "timestamp_field": "crash_date",
        "borough_field": "borough",
        "numeric_fields": ["number_of_persons_injured", "number_of_persons_killed"],
        "expected_daily": 300,
    },
    "SRC-Open-Meteo": {
        "datasets": ["nyc_weather_forecast"],
        "strategy": "daily",
        "timestamp_field": "time",
        "borough_field": None,
        "numeric_fields": ["temperature_2m", "precipitation", "snowfall", "windspeed_10m"],
        "expected_daily": 24,
    },
    "SRC-DCP": {
        "datasets": ["borough_boundaries"],
        "strategy": "static",
        "timestamp_field": None,
        "borough_field": "boro_name",
        "numeric_fields": [],
        "expected_daily": None,
    },
}

# Known valid borough names (case-insensitive canonical form)
VALID_BOROUGHS = {"manhattan", "brooklyn", "queens", "bronx", "staten island"}

# Timestamp anomaly sentinels
EPOCH_ZERO = datetime(1970, 1, 1)
FAR_FUTURE = datetime(2099, 1, 1)


# ── Manifest scanning ──────────────────────────────────────────────────────────

def scan_manifests(
    bucket: storage.Bucket,
    source_id: str,
    dataset: str,
    strategy: str,
) -> list[dict[str, Any]]:
    """Download all manifest files for a source/dataset and return parsed list."""
    prefix = f"bronze/raw/{source_id}/{dataset}/"
    blobs = _list_blobs(bucket, prefix)
    manifests = []
    for blob in blobs:
        name = blob.name.split("/")[-1]
        if name.startswith("manifest"):
            try:
                manifests.append(_download_json(blob))
            except Exception as exc:
                print(f"  [WARN] Failed to parse manifest {blob.name}: {exc}")
    return manifests


def build_coverage_map(manifests: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Summarise manifest list into:
      - total_records: sum of record_count across all manifests
      - partition_count: number of manifest files (days or months)
      - record_counts: list of (partition_key, record_count) sorted by key
      - gaps: partitions with record_count == 0
      - anomaly_low: partitions with record_count below 50% of median
    """
    counts = sorted(
        [(m.get("month_partition") or m.get("ingest_date", "?"), m.get("record_count", 0))
         for m in manifests],
        key=lambda x: x[0],
    )
    if not counts:
        return {"total_records": 0, "partition_count": 0, "record_counts": [], "gaps": [], "anomaly_low": []}

    values = [c for _, c in counts]
    median = statistics.median(values) if values else 0
    threshold = median * 0.5

    return {
        "total_records": sum(values),
        "partition_count": len(counts),
        "record_counts": counts,
        "min_count": min(values),
        "max_count": max(values),
        "median_count": median,
        "gaps": [k for k, v in counts if v == 0],
        "anomaly_low": [k for k, v in counts if 0 < v < threshold],
    }


# ── Data file sampling ─────────────────────────────────────────────────────────

def sample_data_blobs(
    bucket: storage.Bucket,
    source_id: str,
    dataset: str,
    n: int = 3,
) -> list[storage.Blob]:
    """Return up to n data blobs, spread across available files."""
    prefix = f"bronze/raw/{source_id}/{dataset}/"
    blobs = [
        b for b in _list_blobs(bucket, prefix)
        if b.name.split("/")[-1].startswith("data")
        and b.name.endswith(".json")
    ]
    if not blobs:
        return []
    blobs.sort(key=lambda b: b.name)
    if len(blobs) <= n:
        return blobs
    step = len(blobs) // n
    return [blobs[i * step] for i in range(n)]


def profile_records(
    records: list[dict[str, Any]],
    timestamp_field: str | None,
    borough_field: str | None,
    numeric_fields: list[str],
) -> dict[str, Any]:
    """Field-level profiling of a flat list of records."""
    if not records:
        return {"record_count": 0}

    total = len(records)

    # Collect all field names
    all_fields: set[str] = set()
    for r in records:
        if isinstance(r, dict):
            all_fields.update(r.keys())

    # Null rate per field
    null_rates: dict[str, float] = {}
    for field in sorted(all_fields):
        null_count = sum(1 for r in records if not r.get(field))
        null_rates[field] = round(null_count / total, 4)

    result: dict[str, Any] = {
        "record_count": total,
        "field_count": len(all_fields),
        "null_rates": null_rates,
    }

    # Timestamp anomalies
    if timestamp_field:
        ts_analysis = _profile_timestamp(records, timestamp_field, total)
        result["timestamp_analysis"] = ts_analysis

    # Borough dirty values
    if borough_field:
        result["borough_analysis"] = _profile_borough(records, borough_field, total)

    # Numeric distributions
    if numeric_fields:
        result["numeric_distributions"] = {
            f: _profile_numeric(records, f) for f in numeric_fields
        }

    return result


def _profile_timestamp(
    records: list[dict[str, Any]], field: str, total: int
) -> dict[str, Any]:
    parsed = []
    parse_errors = 0
    missing = 0
    epoch_zero_count = 0
    far_future_count = 0

    for r in records:
        raw = r.get(field)
        if not raw:
            missing += 1
            continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00").split("+")[0])
            parsed.append(dt)
            if dt <= EPOCH_ZERO:
                epoch_zero_count += 1
            if dt >= FAR_FUTURE:
                far_future_count += 1
        except (ValueError, TypeError):
            parse_errors += 1

    result: dict[str, Any] = {
        "missing_pct": round(missing / total, 4),
        "parse_error_pct": round(parse_errors / total, 4),
        "epoch_zero_count": epoch_zero_count,
        "far_future_count": far_future_count,
    }
    if parsed:
        result["min"] = min(parsed).isoformat()
        result["max"] = max(parsed).isoformat()
    return result


def _profile_borough(
    records: list[dict[str, Any]], field: str, total: int
) -> dict[str, Any]:
    counter: Counter[str] = Counter()
    missing = 0
    for r in records:
        val = r.get(field)
        if not val:
            missing += 1
        else:
            counter[str(val).strip()] += 1

    dirty = {k: v for k, v in counter.items() if k.lower() not in VALID_BOROUGHS and k != "Unspecified"}
    return {
        "missing_pct": round(missing / total, 4),
        "value_counts": dict(counter.most_common(20)),
        "dirty_values": dirty,
    }


def _profile_numeric(records: list[dict[str, Any]], field: str) -> dict[str, Any]:
    values = []
    for r in records:
        val = r.get(field)
        if val is None:
            continue
        try:
            values.append(float(val))
        except (ValueError, TypeError):
            pass
    if not values:
        return {"missing_pct": 1.0}
    total = len(records)
    return {
        "missing_pct": round((total - len(values)) / total, 4),
        "min": min(values),
        "max": max(values),
        "mean": round(statistics.mean(values), 4),
        "stdev": round(statistics.stdev(values), 4) if len(values) > 1 else 0.0,
    }


# ── Open-Meteo is nested (hourly arrays) ─────────────────────────────────────

def flatten_open_meteo(raw: Any) -> list[dict[str, Any]]:
    """Flatten Open-Meteo hourly dict into a list of per-hour records."""
    if not isinstance(raw, dict):
        return []
    hourly = raw.get("hourly", {})
    times = hourly.get("time", [])
    if not times:
        return []
    keys = [k for k in hourly if k != "time"]
    rows = []
    for i, t in enumerate(times):
        row: dict[str, Any] = {"time": t}
        for k in keys:
            vals = hourly.get(k, [])
            row[k] = vals[i] if i < len(vals) else None
        rows.append(row)
    return rows


# ── Report rendering ──────────────────────────────────────────────────────────

def render_markdown(all_results: dict[str, Any], bucket_name: str) -> str:
    lines = [
        "# Bronze Data Quality Report",
        f"\n**Bucket**: `{bucket_name}`  ",
        f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]

    for source_id, source_data in all_results.items():
        lines += ["---", f"## {source_id}", ""]

        for dataset, ds_data in source_data.items():
            lines += [f"### {dataset}", ""]

            cov = ds_data.get("coverage", {})
            lines += [
                "**Coverage (from manifests)**",
                "",
                "| Metric | Value |",
                "|--------|-------|",
                f"| Total records | {cov.get('total_records', 'N/A'):,} |",
                f"| Partitions found | {cov.get('partition_count', 'N/A')} |",
                f"| Min records/partition | {cov.get('min_count', 'N/A'):,} |",
                f"| Max records/partition | {cov.get('max_count', 'N/A'):,} |",
                f"| Median records/partition | {cov.get('median_count', 'N/A'):,} |",
                "",
            ]

            gaps = cov.get("gaps", [])
            if gaps:
                lines.append(f"> **Gaps (0-record partitions)**: {', '.join(gaps[:10])}")
            anomalies = cov.get("anomaly_low", [])
            if anomalies:
                lines.append(f"> **Anomaly-low partitions (<50% median)**: {', '.join(anomalies[:10])}")

            profile = ds_data.get("sample_profile", {})
            if not profile or profile.get("record_count", 0) == 0:
                lines += ["", "_No sample data available._", ""]
                continue

            lines += [
                "",
                f"**Field Profiling** (sampled {profile['record_count']:,} records across "
                f"{ds_data.get('files_sampled', '?')} file(s))",
                "",
            ]

            # High-null fields
            null_rates = profile.get("null_rates", {})
            high_null = {f: r for f, r in null_rates.items() if r > 0.05}
            if high_null:
                lines += ["**Fields with >5% nulls:**", ""]
                lines.append("| Field | Null % |")
                lines.append("|-------|--------|")
                for f, r in sorted(high_null.items(), key=lambda x: -x[1]):
                    lines.append(f"| `{f}` | {r*100:.1f}% |")
                lines.append("")
            else:
                lines += ["All key fields have <5% nulls. ✓", ""]

            # Timestamp
            ts = profile.get("timestamp_analysis", {})
            if ts:
                lines += ["**Timestamp analysis:**", ""]
                lines.append("| Check | Value |")
                lines.append("|-------|-------|")
                lines.append(f"| Missing | {ts.get('missing_pct', 0)*100:.2f}% |")
                lines.append(f"| Parse errors | {ts.get('parse_error_pct', 0)*100:.2f}% |")
                lines.append(f"| Epoch-zero anomalies | {ts.get('epoch_zero_count', 0)} |")
                lines.append(f"| Far-future anomalies | {ts.get('far_future_count', 0)} |")
                if ts.get("min"):
                    lines.append(f"| Date range | {ts['min'][:10]} → {ts['max'][:10]} |")
                lines.append("")

            # Borough
            bor = profile.get("borough_analysis", {})
            if bor:
                lines += ["**Borough values:**", ""]
                vc = bor.get("value_counts", {})
                if vc:
                    lines.append("| Borough | Count |")
                    lines.append("|---------|-------|")
                    for b, c in list(vc.items())[:10]:
                        lines.append(f"| `{b}` | {c:,} |")
                    lines.append("")
                dirty = bor.get("dirty_values", {})
                if dirty:
                    lines.append(f"> **Dirty borough values**: {dirty}")
                    lines.append("")

            # Numerics
            num = profile.get("numeric_distributions", {})
            if num:
                lines += ["**Numeric distributions:**", ""]
                lines.append("| Field | Min | Max | Mean | Stdev | Missing% |")
                lines.append("|-------|-----|-----|------|-------|---------|")
                for field, stats in num.items():
                    lines.append(
                        f"| `{field}` | {stats.get('min', 'N/A')} | {stats.get('max', 'N/A')} | "
                        f"{stats.get('mean', 'N/A')} | {stats.get('stdev', 'N/A')} | "
                        f"{stats.get('missing_pct', 0)*100:.1f}% |"
                    )
                lines.append("")

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def run_profiler(
    bucket_name: str,
    sources_filter: list[str] | None,
    sample_files: int,
    out_dir: Path,
) -> None:
    client = _gcs_client()
    bucket = client.bucket(bucket_name)
    out_dir.mkdir(parents=True, exist_ok=True)

    target_sources = sources_filter or list(SOURCES.keys())
    all_results: dict[str, Any] = {}

    for source_id in target_sources:
        cfg = SOURCES.get(source_id)
        if not cfg:
            print(f"[WARN] Unknown source: {source_id} — skipping")
            continue

        print(f"\n{'='*60}")
        print(f"Profiling {source_id} (strategy={cfg['strategy']})")
        source_results: dict[str, Any] = {}

        for dataset in cfg["datasets"]:
            print(f"  Dataset: {dataset}")

            # 1. Manifest scan
            print("    Scanning manifests...")
            manifests = scan_manifests(bucket, source_id, dataset, cfg["strategy"])
            coverage = build_coverage_map(manifests)
            print(f"    Found {coverage['partition_count']} partitions, "
                  f"{coverage['total_records']:,} total records")

            # 2. Sample data files
            print(f"    Sampling up to {sample_files} data file(s)...")
            sample_blobs = sample_data_blobs(bucket, source_id, dataset, n=sample_files)
            all_records: list[dict[str, Any]] = []

            for blob in sample_blobs:
                print(f"      → {blob.name}")
                try:
                    raw = _download_json(blob)
                    if source_id == "SRC-Open-Meteo":
                        records = flatten_open_meteo(raw)
                    elif isinstance(raw, list):
                        records = raw
                    elif isinstance(raw, dict):
                        # GeoJSON or wrapped
                        records = raw.get("features", [raw])
                    else:
                        records = []
                    all_records.extend(records)
                except Exception as exc:
                    print(f"      [WARN] Failed to load {blob.name}: {exc}")

            # 3. Profile
            profile = profile_records(
                all_records,
                timestamp_field=cfg.get("timestamp_field"),
                borough_field=cfg.get("borough_field"),
                numeric_fields=cfg.get("numeric_fields", []),
            )

            source_results[dataset] = {
                "coverage": coverage,
                "files_sampled": len(sample_blobs),
                "sample_profile": profile,
            }

        all_results[source_id] = source_results

        # Write per-source JSON
        json_path = out_dir / f"{source_id.lower().replace('-', '_')}_profile.json"
        json_path.write_text(json.dumps(source_results, indent=2, default=str))
        print(f"  Wrote {json_path}")

    # Write summary markdown
    md = render_markdown(all_results, bucket_name)
    md_path = out_dir / "bronze_quality_report.md"
    md_path.write_text(md)
    print(f"\nReport: {md_path}")

    # Print quick summary
    print("\n" + "="*60)
    print("QUICK SUMMARY")
    print("="*60)
    for source_id, source_data in all_results.items():
        for dataset, ds in source_data.items():
            cov = ds.get("coverage", {})
            gaps = cov.get("gaps", [])
            low = cov.get("anomaly_low", [])
            issues = []
            if gaps:
                issues.append(f"{len(gaps)} gaps")
            if low:
                issues.append(f"{len(low)} low-count partitions")
            status = "⚠ " + ", ".join(issues) if issues else "✓ OK"
            print(f"  {source_id}/{dataset}: {cov.get('total_records', 0):>10,} records  {status}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bronze data quality profiler")
    parser.add_argument("--bucket", required=True, help="GCS bucket name (e.g. nyc-uoip-prod)")
    parser.add_argument("--source", nargs="*", help="Source IDs to profile (default: all)")
    parser.add_argument("--sample-files", type=int, default=3,
                        help="Number of data files to sample per dataset (default: 3)")
    parser.add_argument("--out-dir", default="reports", help="Output directory (default: reports/)")
    args = parser.parse_args()

    run_profiler(
        bucket_name=args.bucket,
        sources_filter=args.source,
        sample_files=args.sample_files,
        out_dir=Path(args.out_dir),
    )


if __name__ == "__main__":
    main()
