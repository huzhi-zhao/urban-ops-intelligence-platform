"""Gate expressions parsed out of the ``-- relationships:`` block in sql/ddl.

The DDL header is the only place a table's acceptance criteria are written
down, so the build reads them from there rather than keeping a second copy
that can drift. Only the machine-checkable shape is executed:

    COUNT(*) = 7
    COUNT(*) WHERE is_effective = true = 6

Anything else in that block is prose describing a relationship a human has to
judge (``every distinct silver_service_request.type value ... resolves to a
row here``). Those are printed as unenforced notes. Reporting them as passing
gates would be worse than not having them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# COUNT(*) [WHERE <predicate>] = <n>. The predicate is captured lazily so the
# trailing "= <n>" wins over an "=" inside the predicate itself, which is the
# common case: `COUNT(*) WHERE is_effective = true = 6`.
_COUNT_GATE = re.compile(
    r"^COUNT\(\*\)\s*(?:WHERE\s+(?P<pred>.+?))?\s*=\s*(?P<n>[\d,]+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Gate:
    """One executable acceptance check."""

    source_text: str
    predicate: str | None
    expected: int

    def sql(self, table: str) -> str:
        where = f" WHERE {self.predicate}" if self.predicate else ""
        return f"SELECT COUNT(*) FROM {table}{where}"


def parse_gates(ddl_text: str) -> tuple[list[Gate], list[str]]:
    """Return (executable gates, unenforced prose lines)."""
    lines = ddl_text.splitlines()
    try:
        start = next(
            i for i, ln in enumerate(lines) if ln.strip().lower().startswith("-- relationships:")
        )
    except StopIteration:
        return [], []

    gates: list[Gate] = []
    notes: list[str] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped.startswith("--"):
            break
        body = stripped[2:].strip()
        if not body:
            break
        # A new `-- key:` header ends the block; a continuation line does not.
        if re.match(r"^[a-z_]+ ?\([^)]*\):|^[a-z_]+:", body) and not body.upper().startswith(
            "COUNT("
        ):
            break
        match = _COUNT_GATE.match(body)
        if match:
            gates.append(
                Gate(
                    source_text=body,
                    predicate=match.group("pred"),
                    expected=int(match.group("n").replace(",", "")),
                )
            )
        else:
            notes.append(body)
    return gates, notes
