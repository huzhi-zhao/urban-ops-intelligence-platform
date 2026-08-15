"""Parser for the subset of Trino DDL that ``sql/ddl/*.sql`` is written in.

Not a general SQL parser — it understands exactly the shape those generated
files take. It lives here rather than inside the consistency test because two
consumers need it and a second copy would drift: the test compares the DDL
against the contracts, and ``apply_ddl`` has to know each table's columns,
partition keys and storage layer in order to create, populate and drop it.

Nullability rides in a ``-- not_null`` comment because the Hive connector has
no NOT NULL constraint to carry it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DDL_DIR = REPO_ROOT / "sql" / "ddl"

# A column line inside CREATE TABLE (...): `name TYPE,` or `name TYPE(6),`.
# The name may be double-quoted: `date` and `type` are Trino reserved words and
# several tables use them, so an unquoted-only pattern silently skips those
# columns — and, worse, leaks their `-- not_null` comment onto the next one.
_COLUMN_RE = re.compile(r'^\s{4}"?(\w+)"?\s+([A-Z]+(?:\(\d+\))?),?\s*$')
_PARTITION_RE = re.compile(r"partitioned_by\s*=\s*ARRAY\[(.*?)\]", re.DOTALL)
_LOCATION_RE = re.compile(r"external_location\s*=\s*'([^']+)'")

# The layer is read off the storage path rather than the filename. `silver_*`
# happens to be a reliable prefix today, but the location is what actually
# decides where the data lands, so it is the honest source.
_LAYER_RE = re.compile(r"//[^/]+/(?:.*/)??(silver|gold)/")


@dataclass(frozen=True)
class DdlColumn:
    name: str
    sql_type: str
    not_null: bool


@dataclass(frozen=True)
class Ddl:
    columns: list[DdlColumn]
    partitioned_by: list[str]
    # Populated by parse_ddl_file(); parse_ddl_text() leaves them empty/None
    # because a bare string has no filename to name the table after.
    table: str = ""
    external_location: str = ""

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.columns]

    def column(self, name: str) -> DdlColumn | None:
        return next((c for c in self.columns if c.name == name), None)

    @property
    def layer(self) -> str:
        """``"silver"`` or ``"gold"``, taken from the external location."""
        match = _LAYER_RE.search(self.external_location)
        if match is None:
            raise ValueError(
                f"{self.table or 'DDL'}: external_location "
                f"{self.external_location!r} names neither a silver/ nor a gold/ "
                f"path, so its target schema cannot be derived.",
            )
        return match.group(1)

    @property
    def data_columns(self) -> list[DdlColumn]:
        """Columns excluding the partition keys, in declaration order."""
        return [c for c in self.columns if c.name not in self.partitioned_by]


def parse_ddl_text(text: str) -> Ddl:
    """Parse one ``CREATE TABLE`` statement."""
    body_start = text.index("CREATE TABLE")
    body = text[body_start:]
    # Column definitions run from the opening paren to the closing `)\nWITH`.
    open_paren = body.index("(")
    close = body.index("\n)")
    column_block = body[open_paren + 1 : close]

    columns: list[DdlColumn] = []
    pending_not_null = False
    for line in column_block.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            if stripped == "-- not_null":
                pending_not_null = True
            continue
        match = _COLUMN_RE.match(line)
        if match:
            columns.append(DdlColumn(match.group(1), match.group(2), pending_not_null))
            pending_not_null = False

    tail = body[close:]
    partitioned_by: list[str] = []
    part_match = _PARTITION_RE.search(tail)
    if part_match:
        partitioned_by = re.findall(r"'([^']+)'", part_match.group(1))

    location_match = _LOCATION_RE.search(tail)

    return Ddl(
        columns=columns,
        partitioned_by=partitioned_by,
        external_location=location_match.group(1) if location_match else "",
    )


def parse_ddl_file(path: Path) -> Ddl:
    """Parse ``sql/ddl/<table>.sql``. The table name is the filename stem."""
    parsed = parse_ddl_text(path.read_text())
    return Ddl(
        columns=parsed.columns,
        partitioned_by=parsed.partitioned_by,
        table=path.stem,
        external_location=parsed.external_location,
    )


def load_all_ddl(ddl_dir: Path | None = None) -> dict[str, Ddl]:
    """Parse every DDL file, keyed by table name."""
    directory = ddl_dir or DDL_DIR
    return {path.stem: parse_ddl_file(path) for path in sorted(directory.glob("*.sql"))}
