"""The contract module must name exactly the tables and columns of the contract document."""

import re
from pathlib import Path

from agentic_bi.semantic import contract

CONTRACT_DOC = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "star-schema.md"
TICKED = re.compile(r"`([a-z_]+)`")


def _section(text: str, heading: str) -> list[str]:
    body = text.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in body.splitlines() if line.startswith("| `")]
    assert rows, heading
    return rows


def _documented_tables() -> dict[str, set[str]]:
    text = CONTRACT_DOC.read_text(encoding="utf-8")
    fact = re.search(r"^`(fct_[a-z_]+)`", text, re.MULTILINE).group(1)
    tables = {fact: {TICKED.search(row).group(1) for row in _section(text, "Fact")}}
    for row in _section(text, "Dimensions"):
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        table = TICKED.fullmatch(cells[0]).group(1)
        tables[table] = set(TICKED.findall(cells[1])) | set(TICKED.findall(cells[2]))
    return tables


def test_contract_module_matches_the_contract_document() -> None:
    documented = _documented_tables()
    assert contract.FACT_TABLE in documented
    assert {table: set(columns) for table, columns in contract.TABLES.items()} == documented


def test_every_join_key_and_alias_is_in_the_contract() -> None:
    for table, (dim_key, fact_column) in contract.JOINS.items():
        assert contract.has_column(table, dim_key)
        assert contract.has_column(contract.FACT_TABLE, fact_column)
    assert set(contract.ALIASES) == set(contract.TABLES)
    assert len(set(contract.ALIASES.values())) == len(contract.ALIASES)
    assert contract.has_column(contract.FACT_TABLE, contract.TIME_COLUMN)
