"""Shared plumbing for the metric-feasibility probes.

Every probe under ``scripts/analysis/`` answers one question from
``docs/dev/design/20260808-metric-feasibility-probe.md`` §3.1 by hitting the
public APIs directly — no MinIO, no Silver, no Spark. That constraint is what
makes a probe cheap enough to run before the pipeline that would consume its
answer exists.

What lives here is only the part every probe repeats: resolving a dataset from
``config/sources/*.yaml``, paging a Socrata table, pulling the weather archive,
and the ``--json`` / logging boilerplate around a report function. Probe-specific
analysis stays in the probe module.

Resource ids and domains are never hardcoded — the YAML is the authority, so a
probe stays correct when the deployed city changes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests
from shapely.geometry import Point, shape
from shapely.prepared import prep
from shapely.strtree import STRtree
from shapely.validation import make_valid

from ingestion.backfill.fetchers.open_meteo import OpenMeteoFetcher
from ingestion.config.loader import load_source_config
from ingestion.config.source_config import DatasetConfig

logger = logging.getLogger(__name__)

# Socrata caps a single response; walk the table in pages this size.
PAGE_SIZE = 50_000

# Derived, untracked, safe to delete — same place the backfill plan scripts keep
# their checkpoints.
CACHE_DIR = Path("var/probe-cache")

# Open-Meteo's free tier rate-limits a tight per-zone loop.
HTTP_TOO_MANY_REQUESTS = 429
RETRY_ATTEMPTS = 5
RETRY_BASE_SECONDS = 20

# The weather archive backfills with a lag; a window ending inside this many
# days of today is treated as still open and is never cached.
ARCHIVE_SETTLE_DAYS = 14


# ── Source resolution ────────────────────────────────────────────────────────


def dataset_of(source_id: str, name: str | None = None) -> DatasetConfig:
    """The dataset a probe reads, with its resource id checked.

    ``name`` is required for a source carrying more than one dataset (the
    weather source carries two with different strategies). Omitting it on a
    one-dataset source is the common case; omitting it on a multi-dataset
    source raises rather than picking arbitrarily.
    """
    cfg = load_source_config(source_id)
    if name is not None:
        matches = [d for d in cfg.datasets if d.name == name]
        if not matches:
            available = ", ".join(d.name for d in cfg.datasets)
            raise ValueError(f"{source_id} has no dataset {name!r}; it has: {available}")
        ds = matches[0]
    else:
        if len(cfg.datasets) != 1:
            available = ", ".join(d.name for d in cfg.datasets)
            raise ValueError(
                f"{source_id} carries {len(cfg.datasets)} datasets ({available}); "
                f"pass name= rather than letting this pick arbitrarily",
            )
        ds = cfg.datasets[0]
    return ds


def socrata_dataset_of(source_id: str, name: str | None = None) -> DatasetConfig:
    """As :func:`dataset_of`, additionally requiring a SODA endpoint."""
    ds = dataset_of(source_id, name)
    if not ds.resource_id or not ds.domain:
        raise ValueError(f"{source_id} declares no resource_id/domain")
    return ds


# ── Socrata ──────────────────────────────────────────────────────────────────


def soda_get(ds: DatasetConfig, **params: str) -> list[dict]:
    """One SODA request against the dataset's configured endpoint."""
    url = f"https://{ds.domain}/resource/{ds.resource_id}.json?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - https, config-derived
        return json.load(response)


def soda_walk(ds: DatasetConfig, select: str, where: str | None = None) -> list[dict]:
    """Whole-table walk in pages.

    ``$order=:id`` is required rather than cosmetic: unordered limit/offset
    paging over Socrata has no stable row order, so rows can repeat or vanish
    between pages. Same rule as the static fetcher in ``ingestion/``.
    """
    rows: list[dict] = []
    while True:
        params = {
            "$select": select,
            "$limit": str(PAGE_SIZE),
            "$offset": str(len(rows)),
            "$order": ":id",
        }
        if where:
            params["$where"] = where
        page = soda_get(ds, **params)
        rows.extend(page)
        logger.info("%s: %d rows", ds.resource_id, len(rows))
        if len(page) < PAGE_SIZE:
            return rows


def soda_group_walk(
    ds: DatasetConfig,
    select: str,
    group: str,
    order: str,
    where: str | None = None,
) -> list[dict]:
    """Paged walk over a *grouped* query.

    Separate from :func:`soda_walk` because ``$order=:id`` is not available on
    an aggregate — ``:id`` is not in the group. The caller supplies an ordering
    over the grouping columns instead, which serves the same purpose: without a
    total order the pages of a limit/offset walk are not a partition of the
    result.

    Grouped queries also default to 1,000 rows, so an unpaged one truncates
    silently — the same defect that once made the 311 ``type`` cardinality read
    as 2,000 instead of 3,563.
    """
    rows: list[dict] = []
    while True:
        params = {
            "$select": select,
            "$group": group,
            "$order": order,
            "$limit": str(PAGE_SIZE),
            "$offset": str(len(rows)),
        }
        if where:
            params["$where"] = where
        page = soda_get(ds, **params)
        rows.extend(page)
        logger.info("%s (grouped): %d rows", ds.resource_id, len(rows))
        if len(page) < PAGE_SIZE:
            return rows


# ── Open-Meteo ───────────────────────────────────────────────────────────────


def weather_archive(
    start: date,
    end: date,
    *,
    latitude: float | None = None,
    longitude: float | None = None,
    variables: str | None = None,
) -> list[dict[str, Any]]:
    """Daily weather archive over ``[start, end)``, one record per day.

    Goes through the production fetcher rather than a second HTTP call of its
    own, so a probe reads exactly what the pipeline would ingest — including
    the archive/forecast routing and the parallel-array flattening.

    ``latitude`` / ``longitude`` override the configured point. That is what
    the BO-2 snowfall counterfactual needs: the same archive query at each
    zone centroid instead of the single city point in the YAML.

    ``variables`` overrides the ``daily`` list. The BO-3 follow-up needs wind
    and rain, which the pipeline does not ingest — a probe may ask the upstream
    a wider question than the pipeline does, since nothing here is written to
    Bronze. An override caches under its own key so it can never be served a
    file written for the configured variable list, or vice versa.

    Settled windows are cached under ``var/probe-cache/`` (untracked). 18
    winters is 18 sequential calls of a few seconds each, and a probe gets
    re-run with different thresholds far more often than the archive changes
    — without the cache every parameter sweep re-pays the whole fetch. Delete
    the directory to force a refetch; nothing downstream depends on it.
    """
    ds = dataset_of("SRC-Open-Meteo", "weather_archive")
    params = dict(ds.query_params or {})
    if latitude is not None:
        params["latitude"] = latitude
    if longitude is not None:
        params["longitude"] = longitude
    if variables is not None:
        params["daily"] = variables

    cached = _cache_path(params, start, end, variables)
    if cached and cached.exists():
        logger.info("weather archive [%s, %s) from cache", start, end)
        return json.loads(cached.read_text())

    fetcher = OpenMeteoFetcher(ds.model_copy(update={"query_params": params}), start, end)
    records = _with_backoff(lambda: list(fetcher.fetch()))
    if cached:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(records))
    return records


def _with_backoff(call: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Retry a weather call through Open-Meteo's rate limiter.

    The per-zone counterfactual issues one call per operational zone in a
    tight loop, which trips the free tier's limit partway through. Without a
    retry the probe dies after a dozen zones and the cache keeps only those —
    so the re-run trips the limiter at a *different* zone and never finishes.
    """
    delay = RETRY_BASE_SECONDS
    for attempt in range(RETRY_ATTEMPTS):
        try:
            return call()
        except requests.HTTPError as error:
            status = error.response.status_code if error.response is not None else None
            if status != HTTP_TOO_MANY_REQUESTS or attempt == RETRY_ATTEMPTS - 1:
                raise
            logger.warning("rate limited; retrying in %ds", delay)
            time.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")  # pragma: no cover


def _cache_path(
    params: dict[str, Any], start: date, end: date, variables: str | None = None,
) -> Path | None:
    """Cache file for a settled window, or ``None`` if the window is still open.

    A window reaching into the future or the last few days is not cached: the
    archive backfills with a lag, so caching it would freeze a partial season
    into every later run and quietly shrink the current winter's event count.

    A non-default ``variables`` list gets its own key suffix. The suffix is
    omitted for the configured list so the files earlier probes already wrote
    stay valid — 25 zones x a decade is an hour of rate-limited fetching to
    reproduce, and nothing about those days has changed.
    """
    if end > date.today() - timedelta(days=ARCHIVE_SETTLE_DAYS):
        return None
    key = f"{params.get('latitude')}_{params.get('longitude')}_{start}_{end}"
    if variables is not None:
        key += "_" + hashlib.sha256(variables.encode()).hexdigest()[:8]
    return CACHE_DIR / f"weather_{key}.json"


def winters(first_year: int, last_year: int) -> list[tuple[date, date]]:
    """One ``[start, end)`` window per winter season, Nov 1 → Apr 30.

    A snowfall event is a property of a season, not a calendar year: splitting
    on January would cut every mid-winter event in two, which is the same
    boundary defect the Silver backfill DAG exists to avoid.
    """
    return [
        (date(y, 11, 1), date(y + 1, 5, 1))
        for y in range(first_year, last_year + 1)
    ]


# ── Spatial assignment ───────────────────────────────────────────────────────


class ZoneIndex:
    """Point-in-polygon lookup from a coordinate to an operational zone.

    Built once from the boundary rows and queried per point. Two properties are
    load-bearing rather than incidental:

    - A zone value spans several disjoint polygons (82 pieces over 25 values),
      so the index maps *polygon* → zone label and several entries share a
      label. Unioning first would be wrong here: the r-tree wants the pieces.
    - Invalid upstream polygons are repaired with ``make_valid`` and the
      affected zones are reported on :attr:`repaired`. BO-4's hit rate is
      measured on these same geometries, so a silent repair would change which
      requests land in which zone without anyone knowing an edge moved.
    """

    def __init__(self, rows: list[dict], zone_field: str, geom_field: str) -> None:
        geometries, labels, repaired = [], [], set()
        for row in rows:
            if not row.get(geom_field):
                continue
            geometry = shape(row[geom_field])
            if not geometry.is_valid:
                repaired.add(row[zone_field])
                geometry = make_valid(geometry)
            geometries.append(geometry)
            labels.append(row[zone_field])

        self._tree = STRtree(geometries)
        self._prepared = [prep(g) for g in geometries]
        self._labels = labels
        self.repaired = sorted(repaired)
        self.zones = sorted(set(labels))
        logger.info("zone index: %d polygons over %d zones", len(geometries), len(self.zones))

    def assign(self, longitude: float, latitude: float) -> str | None:
        """The zone containing the point, or ``None`` if it falls outside all."""
        point = Point(longitude, latitude)
        # The r-tree returns bounding-box candidates; contains() is the real test.
        for index in self._tree.query(point):
            if self._prepared[index].contains(point):
                return self._labels[index]
        return None


# ── Statistics ───────────────────────────────────────────────────────────────


def summarise(values: list[float]) -> dict[str, float | None]:
    """Min / median / mean / max — enough to see whether a distribution is flat.

    An empty input reports ``None`` rather than 0: a missing number and a
    measured zero mean different things, and the audit's rule is that a row
    without a number carries no verdict.
    """
    if not values:
        return {"n": 0, "min": None, "median": None, "mean": None, "max": None}
    ordered = sorted(values)
    middle = len(ordered) // 2
    median = (
        ordered[middle]
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / 2
    )
    return {
        "n": len(ordered),
        "min": round(ordered[0], 2),
        "median": round(median, 2),
        "mean": round(sum(ordered) / len(ordered), 2),
        "max": round(ordered[-1], 2),
    }


def pearson(xs: list[float], ys: list[float]) -> float:
    """Correlation coefficient, written out to avoid a numpy dependency here."""
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    dev_x = sum((x - mean_x) ** 2 for x in xs) ** 0.5
    dev_y = sum((y - mean_y) ** 2 for y in ys) ** 0.5
    return covariance / (dev_x * dev_y)


def spearman(xs: list[float], ys: list[float]) -> float:
    """Rank correlation — Pearson over the ranks, ties averaged.

    BO-8's baseline question is about *ordering* stability across events, not
    about magnitudes, so the rank version is the one that answers it.
    """
    return pearson(_ranks(xs), _ranks(ys))


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return ranks


# ── CLI ──────────────────────────────────────────────────────────────────────


def run_probe(
    doc: str,
    build_report: Callable[[argparse.Namespace], dict],
    print_report: Callable[[dict], None],
    add_arguments: Callable[[argparse.ArgumentParser], None] | None = None,
    argv: list[str] | None = None,
) -> int:
    """Argument parsing, logging and ``--json`` output, shared by every probe.

    The JSON output is not a convenience: §2 of the design doc requires every
    reported number to be re-derivable, and the dumped report is what the audit
    row's number is copied from.
    """
    parser = argparse.ArgumentParser(description=doc.splitlines()[0])
    parser.add_argument("--json", metavar="PATH", help="also write the raw result as JSON")
    if add_arguments:
        add_arguments(parser)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    report = build_report(args)
    print_report(report)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, default=str)
        logger.info("wrote %s", args.json)
    return 0


def iso_days(start: date, end: date) -> list[str]:
    """Every day in ``[start, end)`` as ``YYYY-MM-DD``."""
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days)]
