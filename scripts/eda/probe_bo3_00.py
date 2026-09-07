"""Why did fig_bo3_00_daily_window.sql return zero rows?

Throwaway diagnostic, not a figure: it reports the count surviving each
predicate separately, so one run says which one emptied the window instead of
another round of guessing. Delete once FIG-BO3-00 produces rows.

    TRINO_HOST=localhost TRINO_PORT=8090 uv run python -m scripts.eda.probe_bo3_00
"""

from __future__ import annotations

from scripts._env import load_cli_env
from scripts.ddl.apply_ddl import _connect, load_trino_settings, schema_name

WINDOW_CTE = """
WITH days AS (
    SELECT weather_date, COALESCE(snowfall_sum_cm, 0.0) AS snowfall_cm
    FROM silver_weather_archive
    WHERE "date" >= '{start}' AND "date" < '{end}'
),
windows AS (
    SELECT
        weather_date AS window_start_date,
        COUNT(*) OVER w AS days_in_window,
        SUM(snowfall_cm) OVER w AS window_snowfall_cm,
        COUNT_IF(snowfall_cm > 0.0 AND snowfall_cm < 3.0) OVER w AS small_snow_days,
        COUNT_IF(snowfall_cm = 0.0) OVER w AS zero_days
    FROM days
    WINDOW w AS (ORDER BY weather_date ROWS BETWEEN CURRENT ROW AND 13 FOLLOWING)
)
"""

QUERIES: list[tuple[str, str]] = [
    (
        "A. rows in the scan range at all",
        "SELECT COUNT(*), MIN(weather_date), MAX(weather_date) FROM silver_weather_archive "
        "WHERE \"date\" >= '{start}' AND \"date\" < '{end}'",
    ),
    (
        "B. how snowfall_sum_cm is actually distributed",
        "SELECT COUNT(*) AS rows_, COUNT(snowfall_sum_cm) AS non_null, "
        "COUNT_IF(snowfall_sum_cm = 0.0) AS exactly_zero, "
        "COUNT_IF(snowfall_sum_cm > 0.0 AND snowfall_sum_cm < 3.0) AS small, "
        "COUNT_IF(snowfall_sum_cm >= 3.0) AS over_threshold, "
        "ROUND(MAX(snowfall_sum_cm), 2) AS max_cm "
        "FROM silver_weather_archive WHERE \"date\" >= '{start}' AND \"date\" < '{end}'",
    ),
    (
        "C. full 14-day windows",
        WINDOW_CTE + "SELECT COUNT(*) FROM windows WHERE days_in_window = 14",
    ),
    (
        "D. ... that also have >= 3 small-snow days",
        WINDOW_CTE + "SELECT COUNT(*) FROM windows WHERE days_in_window = 14 AND small_snow_days >= 3",
    ),
    (
        "E. ... that also have >= 1 zero day  (the full criterion)",
        WINDOW_CTE
        + "SELECT COUNT(*) FROM windows WHERE days_in_window = 14 AND small_snow_days >= 3 "
        "AND zero_days >= 1",
    ),
    (
        "F. best windows on the small-snow-day count alone, criterion dropped",
        WINDOW_CTE + "SELECT window_start_date, small_snow_days, zero_days, "
        "ROUND(window_snowfall_cm, 1) FROM windows WHERE days_in_window = 14 "
        "ORDER BY small_snow_days DESC, window_snowfall_cm DESC LIMIT 8",
    ),
]

START, END = "2021-11-01", "2023-04-01"


def main() -> int:
    load_cli_env()
    settings = load_trino_settings()
    connection = _connect(settings, schema_name("silver", ""))
    cursor = connection.cursor()
    for label, sql in QUERIES:
        cursor.execute(sql.format(start=START, end=END))
        rows = cursor.fetchall()
        print(f"\n--- {label}")
        for row in rows:
            print("   ", row)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
