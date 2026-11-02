"""The contract with its types, read from docs/contracts/star-schema.md."""

from __future__ import annotations

import pytest

from agentic_bi.powerbi import PowerBIError
from agentic_bi.powerbi.schema import CONTRACT_DOC, load_schema, parse_contract
from agentic_bi.semantic import contract

TEXT = CONTRACT_DOC.read_text(encoding="utf-8")


def test_the_tables_and_columns_equal_the_contract_module() -> None:
    schema = load_schema()
    assert schema.fact == contract.FACT_TABLE
    assert {t.name: tuple(c.name for c in t.columns) for t in schema.tables} == contract.TABLES
    assert [t.name for t in schema.tables][0] == contract.FACT_TABLE


def test_the_types_follow_the_contract() -> None:
    schema = load_schema()
    fact = schema.table("fct_contract_transaction")
    assert fact.column("obligation_amount").type == "DECIMAL(18,2)"
    assert fact.column("action_date").type == "DATE"
    assert fact.column("transaction_key").type == "VARCHAR"
    assert schema.table("dim_date").column("date").type == "DATE"
    assert schema.table("dim_competition").column("is_competed").type == "BOOLEAN"
    date_parts = [c.type for c in schema.table("dim_date").columns[1:]]
    assert date_parts == ["INTEGER"] * 5
    assert schema.table("dim_reporting_recipient").column("reporting_name").type == "VARCHAR"


def test_each_dimension_key_has_the_type_of_its_fact_column() -> None:
    schema = load_schema()
    fact = schema.table(schema.fact)
    for relationship in schema.relationships:
        key = schema.table(relationship.to_table).column(relationship.to_column)
        assert key.type == fact.column(relationship.from_column).type
        assert schema.table(relationship.to_table).key == relationship.to_column


def test_the_relationships_are_the_contract_joins() -> None:
    schema = load_schema()
    pairs = {(r.to_table, (r.to_column, r.from_column)) for r in schema.relationships}
    assert pairs == set(contract.JOINS.items())


def test_an_unknown_type_is_refused() -> None:
    changed = TEXT.replace("| `obligation_amount` | DECIMAL(18,2)", "| `obligation_amount` | MONEY")
    assert changed != TEXT
    with pytest.raises(PowerBIError, match="MONEY"):
        parse_contract(changed)


def test_a_column_the_contract_module_lacks_is_refused() -> None:
    changed = TEXT.replace("`sub_agency_name`", "`sub_agency_name`, `sub_agency_tier`")
    with pytest.raises(PowerBIError, match="disagree"):
        parse_contract(changed)


def test_a_missing_section_is_refused() -> None:
    with pytest.raises(PowerBIError, match="Dimensions"):
        parse_contract(TEXT.replace("## Dimensions", "## Lookups"))
