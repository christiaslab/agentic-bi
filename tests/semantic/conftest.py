"""A synthetic DuckDB fixture with the tables of docs/contracts/star-schema.md.

Every row is made up for the tests; none comes from USAspending. The numbers are small and
round so each expected value in the tests can be added up by hand from the table below.

Recipients SYNR01 and SYNR02 are subsidiaries of parent SYNP01; SYNR03 to SYNR13 have no
parent and report under their own UEI. Competition codes FC and FS count as competed, NC
does not, and T12 has no code. Award W03 is net negative (200.00 then -450.00). T16 falls in
fiscal year 2024 (30 September 2024); every other transaction is in fiscal year 2025.

    tx  award date        amount   type recipient sub-agency psc  naics  comp state
    T01 W01   2024-10-01   1000.00 C    SYNR01    SA1        PSC1 111111 FC   VA
    T02 W01   2024-11-15    500.00 C    SYNR01    SA1        PSC1 111111 FC   VA
    T03 W02   2024-10-20    300.00 D    SYNR02    SA2        PSC2 222222 NC   MD
    T04 W03   2024-12-05    200.00 B    SYNR03    SA1        PSC1 111111 FC   TX
    T05 W03   2025-02-10   -450.00 B    SYNR03    SA1        PSC1 111111 FC   TX
    T06 W04   2025-01-15   1200.00 A    SYNR04    SA2        PSC2 222222 NC   VA
    T07 W05   2025-03-03   1100.00 C    SYNR05    SA1        PSC1 111111 FS   MD
    T08 W06   2025-04-30   1000.00 D    SYNR06    SA2        PSC1 222222 FC   (abroad)
    T09 W07   2025-05-01    900.00 C    SYNR07    SA1        PSC2 111111 NC   VA
    T10 W08   2025-06-12    800.00 A    SYNR08    SA'3;      PSC2 222222 FC   TX
    T11 W09   2025-07-04    700.00 B    SYNR09    SA2        PSC1 111111 NC   MD
    T12 W10   2025-08-08    600.00 C    SYNR10    SA1        PSC2 222222 -    VA
    T13 W11   2025-09-30    500.00 D    SYNR11    SA'3;      PSC1 111111 FS   (abroad)
    T14 W12   2025-09-01    400.00 C    SYNR12    SA2        PSC2 222222 FC   VA
    T15 W13   2024-11-20    300.00 C    SYNR13    SA1        PSC1 111111 NC   TX
    T16 W14   2024-09-30   5000.00 C    SYNR13    SA1        PSC1 111111 FC   VA

The sub-agency code ``SA'3;`` holds a quote and a semicolon on purpose: filtering on it
proves the value travels as a bound parameter and still matches.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest

from agentic_bi.semantic.runner import execute, open_query_connection

DDL = """
CREATE TABLE dim_date (
    "date" DATE PRIMARY KEY,
    fiscal_year INTEGER NOT NULL,
    fiscal_quarter INTEGER NOT NULL,
    fiscal_month INTEGER NOT NULL,
    calendar_year INTEGER NOT NULL,
    calendar_month INTEGER NOT NULL
);
CREATE TABLE dim_recipient (recipient_uei VARCHAR PRIMARY KEY, recipient_name VARCHAR);
CREATE TABLE dim_reporting_recipient (reporting_uei VARCHAR PRIMARY KEY, reporting_name VARCHAR);
CREATE TABLE dim_sub_agency (sub_agency_code VARCHAR PRIMARY KEY, sub_agency_name VARCHAR);
CREATE TABLE dim_psc (psc_code VARCHAR PRIMARY KEY, psc_description VARCHAR);
CREATE TABLE dim_naics (naics_code VARCHAR PRIMARY KEY, naics_description VARCHAR);
CREATE TABLE dim_competition (
    competition_code VARCHAR PRIMARY KEY,
    competition_description VARCHAR,
    is_competed BOOLEAN NOT NULL
);
CREATE TABLE fct_contract_transaction (
    transaction_key VARCHAR PRIMARY KEY,
    award_key VARCHAR NOT NULL,
    action_date DATE NOT NULL,
    obligation_amount DECIMAL(18, 2) NOT NULL,
    award_type_code VARCHAR,
    recipient_uei VARCHAR,
    reporting_uei VARCHAR,
    sub_agency_code VARCHAR,
    psc_code VARCHAR,
    naics_code VARCHAR,
    competition_code VARCHAR,
    pop_state_code VARCHAR
);
"""

# Fiscal fields follow the contract: fiscal year Y runs from Y-1-10-01 to Y-09-30, month 1 is
# October. The range covers every action date above with a margin.
DIM_DATE = """
INSERT INTO dim_date
SELECT
    day::DATE,
    CASE WHEN month(day) >= 10 THEN year(day) + 1 ELSE year(day) END,
    (month(day) + 2) % 12 // 3 + 1,
    (month(day) + 2) % 12 + 1,
    year(day),
    month(day)
FROM range(DATE '2024-09-01', DATE '2025-11-01', INTERVAL 1 DAY) AS days(day)
"""

# SYNR01 and SYNR02 report under their parent SYNP01; every other recipient reports under its
# own UEI (the contract's fallback), so the fact carries the reporting UEI per transaction.
PARENT = ("SYNP01", "Synthetic Alpha Holdings")
RECIPIENTS = [("SYNR01", "Synthetic Alpha East"), ("SYNR02", "Synthetic Alpha West")] + [
    (f"SYNR{i:02d}", f"Synthetic Recipient {i:02d}") for i in range(3, 14)
]
REPORTING_UEI = {"SYNR01": PARENT[0], "SYNR02": PARENT[0]}
REPORTING_RECIPIENTS = [PARENT] + [r for r in RECIPIENTS if r[0] not in REPORTING_UEI]

SUB_AGENCIES = [
    ("SA1", "Synthetic Sub-agency One"),
    ("SA2", "Synthetic Sub-agency Two"),
    ("SA'3;", "Synthetic sub-agency whose code holds a quote and a semicolon"),
]
PSCS = [("PSC1", "Synthetic service"), ("PSC2", "Synthetic product")]
NAICS = [("111111", "Synthetic industry one"), ("222222", "Synthetic industry two")]
COMPETITION = [
    ("FC", "Synthetic code, competed", True),
    ("FS", "Synthetic code, competed under simplified procedures", True),
    ("NC", "Synthetic code, not competed", False),
]

_TX = [
    ("T01", "W01", "2024-10-01", "1000.00", "C", "SYNR01", "SA1", "PSC1", "111111", "FC", "VA"),
    ("T02", "W01", "2024-11-15", "500.00", "C", "SYNR01", "SA1", "PSC1", "111111", "FC", "VA"),
    ("T03", "W02", "2024-10-20", "300.00", "D", "SYNR02", "SA2", "PSC2", "222222", "NC", "MD"),
    ("T04", "W03", "2024-12-05", "200.00", "B", "SYNR03", "SA1", "PSC1", "111111", "FC", "TX"),
    ("T05", "W03", "2025-02-10", "-450.00", "B", "SYNR03", "SA1", "PSC1", "111111", "FC", "TX"),
    ("T06", "W04", "2025-01-15", "1200.00", "A", "SYNR04", "SA2", "PSC2", "222222", "NC", "VA"),
    ("T07", "W05", "2025-03-03", "1100.00", "C", "SYNR05", "SA1", "PSC1", "111111", "FS", "MD"),
    ("T08", "W06", "2025-04-30", "1000.00", "D", "SYNR06", "SA2", "PSC1", "222222", "FC", None),
    ("T09", "W07", "2025-05-01", "900.00", "C", "SYNR07", "SA1", "PSC2", "111111", "NC", "VA"),
    ("T10", "W08", "2025-06-12", "800.00", "A", "SYNR08", "SA'3;", "PSC2", "222222", "FC", "TX"),
    ("T11", "W09", "2025-07-04", "700.00", "B", "SYNR09", "SA2", "PSC1", "111111", "NC", "MD"),
    ("T12", "W10", "2025-08-08", "600.00", "C", "SYNR10", "SA1", "PSC2", "222222", None, "VA"),
    ("T13", "W11", "2025-09-30", "500.00", "D", "SYNR11", "SA'3;", "PSC1", "111111", "FS", None),
    ("T14", "W12", "2025-09-01", "400.00", "C", "SYNR12", "SA2", "PSC2", "222222", "FC", "VA"),
    ("T15", "W13", "2024-11-20", "300.00", "C", "SYNR13", "SA1", "PSC1", "111111", "NC", "TX"),
    ("T16", "W14", "2024-09-30", "5000.00", "C", "SYNR13", "SA1", "PSC1", "111111", "FC", "VA"),
]
TRANSACTIONS = [
    (*row[:3], Decimal(row[3]), *row[4:6], REPORTING_UEI.get(row[5], row[5]), *row[6:])
    for row in _TX
]


def build_fixture(path: Path) -> Path:
    """Write the synthetic star schema to a new DuckDB file and close it."""
    with duckdb.connect(str(path)) as con:
        con.execute(DDL)
        con.execute(DIM_DATE)
        con.executemany("INSERT INTO dim_recipient VALUES (?, ?)", RECIPIENTS)
        con.executemany("INSERT INTO dim_reporting_recipient VALUES (?, ?)", REPORTING_RECIPIENTS)
        con.executemany("INSERT INTO dim_sub_agency VALUES (?, ?)", SUB_AGENCIES)
        con.executemany("INSERT INTO dim_psc VALUES (?, ?)", PSCS)
        con.executemany("INSERT INTO dim_naics VALUES (?, ?)", NAICS)
        con.executemany("INSERT INTO dim_competition VALUES (?, ?, ?)", COMPETITION)
        con.executemany(
            "INSERT INTO fct_contract_transaction VALUES "
            "(?, ?, CAST(? AS DATE), ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            TRANSACTIONS,
        )
    return path


@pytest.fixture(scope="session")
def fixture_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_fixture(tmp_path_factory.mktemp("semantic") / "synthetic.duckdb")


@pytest.fixture
def conn(fixture_db: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    connection = open_query_connection(fixture_db)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def run(conn: duckdb.DuckDBPyConnection) -> Callable[[dict[str, Any]], list[tuple[Any, ...]]]:
    """Compile and run a structured query on the read-only fixture; return its rows."""

    def _run(query: dict[str, Any]) -> list[tuple[Any, ...]]:
        return execute(conn, query).rows

    return _run
