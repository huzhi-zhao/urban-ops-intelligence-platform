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
        "A. partition keys as the metastore has them (cheap: metastore only)",
        'SELECT COUNT(*) AS partitions_, MIN("date") AS min_key, MAX("date") AS max_key '
        'FROM "silver_weather_archive$partitions"',
    ),
    (
        "B. a sample of actual partition key values -- what shape are they?",
        'SELECT "date" FROM "silver_weather_archive$partitions" ORDER BY "date" LIMIT 5',
    ),
    (
        "C. ... and the newest five",
        'SELECT "date" FROM "silver_weather_archive$partitions" ORDER BY "date" DESC LIMIT 5',
    ),
    (
        "D. any partition key that looks like the 2021-2022 winter",
        "SELECT COUNT(*) FROM \"silver_weather_archive$partitions\" WHERE \"date\" LIKE '2021-1%'",
    ),
    (
        "E. read one real row from the newest partition (proves data, not just metadata)",
        'SELECT weather_date, snowfall_sum_cm, "date" FROM silver_weather_archive '
        'WHERE "date" = (SELECT MAX("date") FROM "silver_weather_archive$partitions") LIMIT 3',
    ),
    (
        "F. does weather_date agree with the partition key it sits under?",
        'SELECT "date", MIN(weather_date), MAX(weather_date), COUNT(*) FROM silver_weather_archive '
        "WHERE \"date\" LIKE '2022-01%' GROUP BY \"date\" ORDER BY \"date\" LIMIT 5",
    ),
]

START, END = "2021-11-01", "2023-04-01"


def main() -> int:
    load_cli_env()
    settings = load_trino_settings()
    connection = _connect(settings, schema_name("silver", ""))
    cursor = connection.cursor()
    for label, sql in QUERIES:
        print(f"\n--- {label}")
        try:
            cursor.execute(sql.format(start=START, end=END))
            for row in cursor.fetchall():
                print("   ", row)
        except Exception as exc:  # noqa: BLE001 - a probe reports, never aborts
            print("    FAILED:", str(exc)[:300])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
