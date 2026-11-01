"""The built database holds exactly the tables and columns of the star-schema contract."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import pytest

from agentic_bi import warehouse
from agentic_bi.ingest import spec

ROOT = warehouse.ROOT
CONTRACT = ROOT / "docs" / "contracts" / "star-schema.md"

# docs/contracts/star-schema.md, transcribed: table -> (column, DuckDB type), in order.
# The contract gives the fact's types; dimension columns are VARCHAR unless it says
# otherwise, and the date fields are a DATE and integers.
EXPECTED = {
    "fct_contract_transaction": [
        ("transaction_key", "VARCHAR"),
        ("award_key", "VARCHAR"),
        ("action_date", "DATE"),
        ("obligation_amount", "DECIMAL(18,2)"),
        ("award_type_code", "VARCHAR"),
        ("recipient_uei", "VARCHAR"),
        ("reporting_uei", "VARCHAR"),
        ("sub_agency_code", "VARCHAR"),
        ("psc_code", "VARCHAR"),
        ("naics_code", "VARCHAR"),
        ("competition_code", "VARCHAR"),
        ("pop_state_code", "VARCHAR"),
    ],
    "dim_date": [
        ("date", "DATE"),
        ("fiscal_year", "INTEGER"),
        ("fiscal_quarter", "INTEGER"),
        ("fiscal_month", "INTEGER"),
        ("calendar_year", "INTEGER"),
        ("calendar_month", "INTEGER"),
    ],
    "dim_recipient": [("recipient_uei", "VARCHAR"), ("recipient_name", "VARCHAR")],
    "dim_reporting_recipient": [("reporting_uei", "VARCHAR"), ("reporting_name", "VARCHAR")],
    "dim_sub_agency": [("sub_agency_code", "VARCHAR"), ("sub_agency_name", "VARCHAR")],
    "dim_psc": [("psc_code", "VARCHAR"), ("psc_description", "VARCHAR")],
    "dim_naics": [("naics_code", "VARCHAR"), ("naics_description", "VARCHAR")],
    "dim_competition": [
        ("competition_code", "VARCHAR"),
        ("competition_description", "VARCHAR"),
        ("is_competed", "BOOLEAN"),
    ],
}


def _columns(con: duckdb.DuckDBPyConnection, schema: str) -> dict[str, list[tuple[str, str]]]:
    rows = con.execute(
        "select table_name, column_name, data_type from information_schema.columns "
        "where table_schema = ? order by table_name, ordinal_position",
        [schema],
    ).fetchall()
    tables: dict[str, list[tuple[str, str]]] = {}
    for table, column, data_type in rows:
        tables.setdefault(table, []).append((column, data_type))
    return tables


def test_the_transcription_names_only_what_the_contract_names() -> None:
    text = CONTRACT.read_text(encoding="utf-8")
    for table, columns in EXPECTED.items():
        assert f"`{table}`" in text, table
        for column, _ in columns:
            assert f"`{column}`" in text, (table, column)


def test_the_contract_schema_holds_exactly_the_contract_tables_and_columns(con) -> None:
    assert _columns(con, warehouse.CONTRACT_SCHEMA) == EXPECTED


def test_no_table_holds_an_excluded_column(con) -> None:
    columns = con.execute("select column_name from information_schema.columns").fetchall()
    names = [row[0] for row in columns]
    assert names
    assert spec.excluded_in(names) == []


def test_the_built_file_needs_no_external_access(con) -> None:
    for table in EXPECTED:
        assert con.execute(f"select count(*) from {table}").fetchone()[0] > 0, table
    with pytest.raises(duckdb.Error):
        con.execute("select count(*) from read_parquet(?)", [str(warehouse.SNAPSHOT_PARQUET)])


def test_dim_date_has_every_day_of_the_fiscal_year(con) -> None:
    rows, first, last, days = con.execute(
        'select count(*), min("date"), max("date"), count(distinct "date") from dim_date'
    ).fetchone()
    assert first == dt.date.fromisoformat(spec.START_DATE)
    assert last == dt.date.fromisoformat(spec.END_DATE)
    assert rows == days == (last - first).days + 1 == 365
    years = con.execute("select distinct fiscal_year from dim_date").fetchall()
    assert years == [(spec.FISCAL_YEAR,)]


def test_dim_date_fiscal_fields_start_in_october(con) -> None:
    by_month = dict(
        con.execute(
            "select calendar_month, list(distinct (fiscal_month, fiscal_quarter)) "
            "from dim_date group by calendar_month"
        ).fetchall()
    )
    expected = {10: 1, 11: 2, 12: 3, 1: 4, 2: 5, 3: 6, 4: 7, 5: 8, 6: 9, 7: 10, 8: 11, 9: 12}
    for calendar_month, fiscal_month in expected.items():
        quarter = (fiscal_month - 1) // 3 + 1
        assert by_month[calendar_month] == [(fiscal_month, quarter)], calendar_month
    mismatched = con.execute(
        'select count(*) from dim_date where calendar_year <> year("date") '
        'or calendar_month <> month("date")'
    ).fetchone()[0]
    assert mismatched == 0


def test_fiscal_year_agrees_with_the_source_on_every_transaction(con) -> None:
    disagree = con.execute(
        "select count(*) from staging.contract_transaction s "
        'left join dim_date d on d."date" = s.action_date '
        "where d.fiscal_year is distinct from s.action_date_fiscal_year"
    ).fetchone()[0]
    assert disagree == 0


def test_one_row_per_recipient_and_per_reporting_uei(con, source) -> None:
    recipients, reporting = source.execute(
        "select count(distinct recipient_uei), "
        "count(distinct coalesce(recipient_parent_uei, recipient_uei)) from snapshot"
    ).fetchone()
    assert con.execute("select count(*) from dim_recipient").fetchone()[0] == recipients
    assert con.execute("select count(*) from dim_reporting_recipient").fetchone()[0] == reporting


def test_the_fact_carries_the_reporting_parent_of_each_transaction(con, source) -> None:
    built = dict(
        con.execute(
            "select transaction_key, reporting_uei from fct_contract_transaction"
        ).fetchall()
    )
    expected = dict(
        source.execute(
            "select contract_transaction_unique_key, coalesce(recipient_parent_uei, recipient_uei) "
            "from snapshot"
        ).fetchall()
    )
    assert built == expected
    changing = con.execute(
        "select count(*) from (select recipient_uei from fct_contract_transaction "
        "group by recipient_uei having count(distinct reporting_uei) > 1)"
    ).fetchone()[0]
    assert changing > 0  # some recipients change parent during the year; S1 counted 13


def test_the_fact_keeps_every_source_value(con, source) -> None:
    columns = {
        "transaction_key": "contract_transaction_unique_key",
        "award_key": "contract_award_unique_key",
        "action_date": "action_date",
        "obligation_amount": "federal_action_obligation",
        "award_type_code": "award_type_code",
        "recipient_uei": "recipient_uei",
        "sub_agency_code": "awarding_sub_agency_code",
        "psc_code": "product_or_service_code",
        "naics_code": "naics_code",
        "competition_code": "extent_competed_code",
        "pop_state_code": "primary_place_of_performance_state_code",
    }
    built = con.execute(
        f"select {', '.join(columns)} from fct_contract_transaction order by transaction_key"
    ).fetchall()
    expected = source.execute(
        f"select {', '.join(columns.values())} from snapshot "
        "order by contract_transaction_unique_key"
    ).fetchall()
    # pop_state_code is null abroad by rule; on this snapshot the source state is null there too
    assert built == expected


def test_the_default_database_is_under_data_tmp() -> None:
    assert warehouse.DEFAULT_DATABASE.parent == ROOT / "data" / "tmp"
    config = warehouse.project_config()
    assert Path(config.get_connection().database) == warehouse.DEFAULT_DATABASE


def test_the_project_sends_no_usage_events() -> None:
    # the project's network is package installs and the USAspending hosts (AGENTS.md)
    assert warehouse.project_config().disable_anonymized_analytics is True
