"""The star-schema contract with its column types, read from ``docs/contracts/star-schema.md``.

The contract document is the source: it names every table and column, gives the fact's
types, and marks a dimension column's type in brackets where it is not text
(``is_competed`` (BOOLEAN)). For the rest the module applies the reading the warehouse tests
already apply to the same document:

- a dimension key has the type of the fact column that joins it, so ``dim_date.date`` is a
  DATE like ``action_date`` and every other key is VARCHAR;
- the fiscal and calendar fields of ``dim_date`` (year, quarter, month) are integers;
- any other dimension column is VARCHAR.

The parsed names must equal ``agentic_bi.semantic.contract``, which the compiler uses; the
types are checked against the built warehouse and the exported Parquet files by the tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from agentic_bi.semantic import contract

from . import ROOT, PowerBIError

CONTRACT_DOC = ROOT / "docs" / "contracts" / "star-schema.md"

TYPES = ("VARCHAR", "DATE", "DECIMAL(18,2)", "INTEGER", "BOOLEAN")
_TICKED = re.compile(r"`([a-z_]+)`")
_LEADING = re.compile(r"\s*`([a-z_]+)`(?:\s*\(([A-Z]+(?:\(\d+,\s*\d+\))?)\))?")
_DATE_PARTS = re.compile(r"^(fiscal|calendar)_(year|quarter|month)$")


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    meaning: str | None = None


@dataclass(frozen=True)
class Table:
    name: str
    key: str | None
    columns: tuple[Column, ...]

    def column(self, name: str) -> Column:
        for column in self.columns:
            if column.name == name:
                return column
        raise KeyError(f"{self.name}.{name}")


@dataclass(frozen=True)
class Relationship:
    """Many-to-one: ``from`` is a fact column, ``to`` the key of a dimension."""

    from_table: str
    from_column: str
    to_table: str
    to_column: str


@dataclass(frozen=True)
class StarSchema:
    fact: str
    tables: tuple[Table, ...]
    relationships: tuple[Relationship, ...]

    def table(self, name: str) -> Table:
        for table in self.tables:
            if table.name == name:
                return table
        raise KeyError(name)


def _normal_type(text: str) -> str:
    value = re.sub(r"\s+", "", text.strip().upper())
    if value not in TYPES:
        raise PowerBIError(f"the contract type {text!r} has no Power BI mapping")
    return value


def _section(text: str, heading: str) -> list[str]:
    parts = text.split(f"## {heading}\n", 1)
    if len(parts) != 2:
        raise PowerBIError(f"the contract has no section {heading!r}")
    body = parts[1].split("\n## ", 1)[0]
    rows = [line for line in body.splitlines() if line.startswith("| `")]
    if not rows:
        raise PowerBIError(f"the contract section {heading!r} has no table rows")
    return rows


def _cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def parse_contract(text: str) -> StarSchema:
    """The contract's tables, columns, types and joins, in the document's order."""
    found = re.search(r"^`(fct_[a-z_]+)`", text, re.MULTILINE)
    if found is None:
        raise PowerBIError("the contract names no fact table")
    fact_name = found.group(1)
    fact_columns = []
    for row in _section(text, "Fact"):
        name_cell, type_cell, meaning = _cells(row)[:3]
        name = _TICKED.fullmatch(name_cell)
        if name is None:
            raise PowerBIError(f"cannot read the fact row {row!r}")
        # "VARCHAR, unique": the type comes first, a constraint may follow its comma
        kind = _normal_type(re.split(r",\s*(?=[a-z])", type_cell, maxsplit=1)[0])
        fact_columns.append(Column(name.group(1), kind, meaning))
    fact = Table(fact_name, None, tuple(fact_columns))

    joins = {table: (key, fact_column) for table, (key, fact_column) in contract.JOINS.items()}
    tables = [fact]
    for row in _section(text, "Dimensions"):
        cells = _cells(row)
        table_name = _TICKED.fullmatch(cells[0])
        key_name = _TICKED.fullmatch(cells[1])
        if table_name is None or key_name is None:
            raise PowerBIError(f"cannot read the dimension row {row!r}")
        name, key = table_name.group(1), key_name.group(1)
        if name not in joins or joins[name][0] != key:
            raise PowerBIError(f"{name} joins the fact on {key}, which the contract module lacks")
        columns = [Column(key, fact.column(joins[name][1]).type)]
        # A column opens a comma-separated item; ticked names inside its prose are not columns.
        for part in cells[2].split(","):
            match = _LEADING.match(part)
            if match is None:
                continue
            column, marked = match.groups()
            if marked:
                kind = _normal_type(marked)
            elif name == "dim_date" and _DATE_PARTS.match(column):
                kind = "INTEGER"
            else:
                kind = "VARCHAR"
            columns.append(Column(column, kind))
        tables.append(Table(name, key, tuple(columns)))

    relationships = tuple(
        Relationship(fact_name, fact_column, table, key)
        for table, (key, fact_column) in contract.JOINS.items()
    )
    schema = StarSchema(fact_name, tuple(tables), relationships)
    _check_against_module(schema)
    return schema


def _check_against_module(schema: StarSchema) -> None:
    documented = {table.name: tuple(c.name for c in table.columns) for table in schema.tables}
    module = {table: tuple(columns) for table, columns in contract.TABLES.items()}
    if schema.fact != contract.FACT_TABLE or documented != module:
        raise PowerBIError(
            "docs/contracts/star-schema.md and agentic_bi.semantic.contract disagree on the "
            "tables or columns; fix the contract first"
        )


@lru_cache(maxsize=1)
def _default() -> StarSchema:
    return parse_contract(CONTRACT_DOC.read_text(encoding="utf-8"))


def load_schema(path: Path | str | None = None) -> StarSchema:
    """The contract as data; the project's own document when ``path`` is None."""
    if path is None:
        return _default()
    return parse_contract(Path(path).read_text(encoding="utf-8"))
