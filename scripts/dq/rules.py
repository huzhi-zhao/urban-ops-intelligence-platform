"""Parse and validate the out-of-pipeline DQ rule list (`config/dq/rules.yaml`).

Rules live in configuration, not in code: a threshold is business semantics
(city-agnostic guardrail §1), and the audit runs daily against numbers that
move when the upstream moves. A rule the auditor has to be redeployed to
change is a rule that gets muted instead of updated.

What this module owns is the *shape* of a rule and the comparator semantics.
The values themselves come from measured baselines — L3-c's per-column null
rates, the 185 four-family assertions, and the row counts recorded in each
launch doc. Design: docs/dev/design/20260822-out-of-pipeline-dq-audit.md §5.3.

🔴 **No out-of-pipeline row-count rule may be an equality** (design §4.2). The
exact-equality gates stay in the pipeline, where the expected value and the
observed value come from the same build. Out here the audit runs daily against
a table that gets rebuilt and an upstream that appends and back-corrects, so an
equality is guaranteed to be wrong eventually — and a rule that is wrong daily
gets silenced. Enforced by `_validate` and nailed by a unit test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES_PATH = REPO_ROOT / "config" / "dq" / "rules.yaml"

LAYERS = ("bronze", "silver", "gold")
DIMENSIONS = ("structural", "statistical", "business", "cross_layer")
SEVERITIES = ("error", "warn")
CADENCES = ("daily", "weekly", "manual")

# Built-in check types. `sql` is the escape hatch: one scalar-returning query.
CHECK_TYPES = (
    "ddl_assertions",  # re-run scripts/gold/dq_assertions for one/all tables
    "row_count",  # COUNT(*) of a table
    "null_rate",  # per-column null percentage
    "empty_tables",  # how many selected tables have zero rows
    "all_null_columns",  # how many columns are null in every row
    "partition_freshness",  # days between today and the newest partition
    "sql",  # custom scalar query
)

COMPARATORS = (">=", "<=", "==", "between", "pct_change", "pct_point_change")

# Comparators whose `expected` is a two-element bound pair rather than a scalar.
_PAIR_COMPARATORS = ("between",)

# Checks that count rows in a table. These are the ones §4.2 forbids from
# being equalities; `rows_failed = 0`-shaped checks are not row counts.
_ROW_COUNT_CHECKS = ("row_count",)

_SILVER_REF = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z0-9_.\"{} ]*silver_\w+)", re.IGNORECASE)
_SILVER_PLACEHOLDER = "{{ silver }}."
# R1: a committed query that reads Silver prunes partitions on the partition
# column. `open_ts_utc` filters rows and prunes nothing.
_SILVER_PARTITION_COLUMN = "open_date_local"


class DqRuleError(ValueError):
    """A rule file that cannot be trusted to run. Raised at load time."""


@dataclass(frozen=True)
class Rule:
    """One out-of-pipeline check."""

    id: str
    layer: str
    table: str
    dimension: str
    check: dict[str, Any]
    comparator: str
    expected: Any
    severity: str
    cadence: str
    note: str = ""

    @property
    def check_type(self) -> str:
        return str(self.check["type"])

    def runs_on(self, cadence: str) -> bool:
        """Whether a run of the given cadence should execute this rule.

        A daily run executes only the daily rules; a weekly run also picks up
        the daily ones, because a weekly sweep that skipped them would leave a
        gap in the trend on the day it ran. `manual` is never implied.
        """
        if cadence == "manual":
            return True
        if cadence == "weekly":
            return self.cadence in ("daily", "weekly")
        return self.cadence == "daily"


def evaluate(rule: Rule, observed: float | None, previous: float | None = None) -> bool:
    """Apply the rule's comparator. `previous` is the last logged observation.

    A `pct_change` rule with no previous observation **passes**: the first run
    is the trend's point zero and has nothing to compare against. Failing it
    would mean every fresh `dq_audit_log` starts red.
    """
    if observed is None:
        return False
    if rule.comparator == ">=":
        return observed >= float(rule.expected)
    if rule.comparator == "<=":
        return observed <= float(rule.expected)
    if rule.comparator == "==":
        return observed == float(rule.expected)
    if rule.comparator == "between":
        low, high = rule.expected
        return float(low) <= observed <= float(high)
    if rule.comparator == "pct_point_change":
        # For observations that are already percentages. "5 percentage points"
        # and "5 percent" are different tolerances, and the seven documented
        # null rates (93.46% … 10.53%) are the reason both exist: ±5% of 93.46
        # is ±4.7 points, ±5% of 10.53 is ±0.5.
        if previous is None:
            return True
        return abs(observed - float(previous)) <= float(rule.expected)
    if rule.comparator == "pct_change":
        if previous is None:
            return True
        if previous == 0:
            # No denominator. Any move off zero is a change worth seeing, and
            # 0 -> 0 is not.
            return observed == 0
        change = 100.0 * (observed - previous) / abs(previous)
        return abs(change) <= float(rule.expected)
    raise DqRuleError(f"{rule.id}: unknown comparator {rule.comparator!r}")


def _require(rule: dict[str, Any], key: str, where: str) -> Any:
    if key not in rule:
        raise DqRuleError(f"{where}: missing required field {key!r}")
    return rule[key]


def _validate_sql(rule_id: str, sql: str) -> None:
    for target in _SILVER_REF.findall(sql):
        cleaned = target.strip()
        if _SILVER_PLACEHOLDER not in cleaned:
            raise DqRuleError(
                f"{rule_id}: reads {cleaned!r} bare. Every Silver table is reached "
                f"through '{_SILVER_PLACEHOLDER}' (gold-sql.md R6) — a bare name "
                f"resolves in the Gold schema and dies with TABLE_NOT_FOUND.",
            )
        if _SILVER_PARTITION_COLUMN not in sql:
            raise DqRuleError(
                f"{rule_id}: reads Silver without a {_SILVER_PARTITION_COLUMN} "
                f"predicate (gold-sql.md R1). A whole-table scan of "
                f"silver_service_request's 4,878 partitions times out.",
            )


def _validate(raw: dict[str, Any], index: int) -> Rule:
    where = f"rules[{index}]"
    rule_id = str(_require(raw, "id", where))
    where = f"rule {rule_id}"

    for field_name, allowed in (
        ("layer", LAYERS),
        ("dimension", DIMENSIONS),
        ("severity", SEVERITIES),
        ("cadence", CADENCES),
    ):
        value = str(_require(raw, field_name, where))
        if value not in allowed:
            raise DqRuleError(f"{where}: {field_name}={value!r} not one of {allowed}")

    check = _require(raw, "check", where)
    if not isinstance(check, dict) or "type" not in check:
        raise DqRuleError(f"{where}: check must be a mapping carrying a 'type'")
    check_type = str(check["type"])
    if check_type not in CHECK_TYPES:
        raise DqRuleError(f"{where}: unknown check type {check_type!r}, expected {CHECK_TYPES}")
    if check_type == "sql":
        if "sql" not in check:
            raise DqRuleError(f"{where}: a 'sql' check must carry a 'sql' field")
        _validate_sql(rule_id, str(check["sql"]))

    comparator = str(_require(raw, "comparator", where))
    if comparator not in COMPARATORS:
        raise DqRuleError(f"{where}: comparator={comparator!r} not one of {COMPARATORS}")

    expected = _require(raw, "expected", where)
    if comparator in _PAIR_COMPARATORS:
        if not isinstance(expected, list) or len(expected) != 2:
            raise DqRuleError(f"{where}: comparator {comparator!r} expects [low, high]")
    elif isinstance(expected, list):
        raise DqRuleError(f"{where}: comparator {comparator!r} expects a scalar")

    if check_type in _ROW_COUNT_CHECKS and comparator == "==":
        raise DqRuleError(
            f"{where}: an out-of-pipeline row-count rule may not be an equality "
            f"(design §4.2). Gold gets rebuilt and the upstream appends, so the "
            f"expected value goes stale and the rule gets muted. Use a lower "
            f"bound (baseline × 0.9) plus a pct_change rule for the drift.",
        )

    return Rule(
        id=rule_id,
        layer=str(raw["layer"]),
        table=str(raw.get("table", "*")),
        dimension=str(raw["dimension"]),
        check=dict(check),
        comparator=comparator,
        expected=expected,
        severity=str(raw["severity"]),
        cadence=str(raw["cadence"]),
        note=str(raw.get("note", "")),
    )


def load_rules(path: Path | None = None) -> list[Rule]:
    """Parse the rule file, validating every rule before returning any."""
    target = path or RULES_PATH
    document = yaml.safe_load(target.read_text()) or {}
    raw_rules = document.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise DqRuleError(f"{target}: no 'rules' list")

    rules = [_validate(raw, i) for i, raw in enumerate(raw_rules)]

    seen: set[str] = set()
    for rule in rules:
        if rule.id in seen:
            raise DqRuleError(f"duplicate rule id {rule.id!r} — ids key the trend in dq_audit_log")
        seen.add(rule.id)
    return rules
