"""Read the four talking-point distributions off the scoring chain (L3-c).

These are not gates — nothing here can fail a build. They are the numbers the
BO-8 narrative is actually made of, and the launch doc's "讲稿素材" table wants
them verbatim:

* how often the model's ordering differs from the baseline's (`rank_delta`),
* which attribution rule fired how often,
* the `load_level` distribution, split by weight profile because the two
  profiles have different ceilings (100 vs 70) and pooling them would invent a
  skew that is an artefact of the split (gold-sql.md R3, same reasoning).

Read-only, Gold-only: the largest table it touches is 1,298 rows and nothing
here reads Silver, so R1 does not apply.

    TRINO_HOST=localhost TRINO_PORT=8090 uv run python -m scripts.gold.talking_points
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from scripts._env import load_cli_env
from scripts.ddl.apply_ddl import TrinoConfigError, _connect, load_trino_settings, schema_name
from scripts.gold.build_gold import HOST_SHELL_HINT

# (heading, sql). Every query groups rather than filters, so a category with
# zero rows is visible as a missing row instead of being silently folded into a
# ratio — RULE-BALANCED at 0 is a finding (launch §4.7 item 3), not a blank.
QUERIES: tuple[tuple[str, str], ...] = (
    (
        "rank_delta —— 模型排序 vs 基线排序（每个 model_version）",
        "SELECT model_version,"
        " SUM(CASE WHEN rank_delta > 0 THEN 1 ELSE 0 END) AS better,"
        " SUM(CASE WHEN rank_delta = 0 THEN 1 ELSE 0 END) AS same,"
        " SUM(CASE WHEN rank_delta < 0 THEN 1 ELSE 0 END) AS worse,"
        " COUNT(*) AS cells"
        " FROM fact_recommendation GROUP BY model_version ORDER BY model_version",
    ),
    (
        "attribution_rule_id 命中分布（每个 model_version）",
        "SELECT model_version, attribution_rule_id, COUNT(*) AS cells"
        " FROM fact_recommendation GROUP BY model_version, attribution_rule_id"
        " ORDER BY model_version, cells DESC",
    ),
    (
        "load_level 分布（按 score_weight_profile 分开）",
        "SELECT score_weight_profile, load_level, COUNT(*) AS cells,"
        " ROUND(MIN(load_score), 2) AS min_score, ROUND(MAX(load_score), 2) AS max_score"
        " FROM fact_winter_event_zone_load"
        " GROUP BY score_weight_profile, load_level"
        " ORDER BY score_weight_profile, min_score",
    ),
    (
        "三因子的实际取值范围（BO-6 的效应量是按这把尺讲的）",
        "SELECT score_status,"
        " ROUND(MIN(request_forecast_factor), 3) AS demand_min,"
        " ROUND(MAX(request_forecast_factor), 3) AS demand_max,"
        " ROUND(MIN(rank_factor), 3) AS rank_min, ROUND(MAX(rank_factor), 3) AS rank_max,"
        " ROUND(MIN(weather_severity_factor), 3) AS weather_min,"
        " ROUND(MAX(weather_severity_factor), 3) AS weather_max"
        " FROM fact_winter_event_zone_load GROUP BY score_status ORDER BY score_status",
    ),
)


def render(headers: list[str], rows: list[tuple[Any, ...]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for row in rows:
        cells = [f"{v:,}" if isinstance(v, int) else str(v) for v in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--location-prefix", default="", help="smoke namespace, as in apply_ddl")
    return parser


def main(argv: list[str] | None = None) -> int:
    load_cli_env()
    args = build_parser().parse_args(argv)
    try:
        settings = load_trino_settings()
    except TrinoConfigError as exc:
        print(f"{exc}\n\n{HOST_SHELL_HINT}", file=sys.stderr)
        return 2

    gold_schema = schema_name("gold", args.location_prefix)
    print(f"schema={gold_schema}", file=sys.stderr)
    try:
        conn = _connect(settings, gold_schema)
    except Exception as exc:  # noqa: BLE001
        print(f"\n{HOST_SHELL_HINT}\n\nunderlying error: {type(exc).__name__}: {exc}")
        return 2

    for heading, sql in QUERIES:
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = cursor.fetchall()
        headers = [d[0] for d in cursor.description]
        print(f"\n#### {heading}\n")
        print(render(headers, rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
