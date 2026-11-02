"""A small synthetic star schema for the agent tests, with labels that test the delimiter.

Every row is made up; none comes from USAspending. Recipients R01 and R02 report under their
parent P01; the others under their own UEI. FC counts as competed, NC does not, A06 has no
competition code, and A06 is performed abroad (no state). Fiscal year 2025 only.

    tx  award date        amount   type recipient reporting sub psc  naics  comp state
    A01 W01   2024-10-15  1000.00  C    R01       P01       SA1 PSC1 111111 FC   VA
    A02 W01   2024-12-01   500.00  C    R01       P01       SA1 PSC1 111111 FC   VA
    A03 W02   2025-01-10   800.00  D    R02       P01       SA1 PSC2 222222 NC   MD
    A04 W03   2025-02-20   700.00  B    R03       R03       SA2 PSC1 111111 FC   TX
    A05 W03   2025-03-05  -200.00  B    R03       R03       SA2 PSC1 111111 FC   TX
    A06 W04   2025-05-01   400.00  A    R04       R04       SA2 PSC2 222222 -    (abroad)
    A07 W05   2025-07-04   300.00  C    R05       R05       SA1 PSC2 111111 NC   VA
    A08 W06   2025-09-30   100.00  D    R06       R06       SA1 PSC1 222222 FC   MD

Totals: obligations 3600.00; awards 6; by reporting recipient P01 2300.00, R03 500.00, R04
400.00, R05 300.00, R06 100.00; competed 2100.00 of 3600.00.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import duckdb

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

DIM_DATE = """
INSERT INTO dim_date
SELECT
    day::DATE,
    CASE WHEN month(day) >= 10 THEN year(day) + 1 ELSE year(day) END,
    (month(day) + 2) % 12 // 3 + 1,
    (month(day) + 2) % 12 + 1,
    year(day),
    month(day)
FROM range(DATE '2024-10-01', DATE '2025-10-01', INTERVAL 1 DAY) AS days(day)
"""

REPORTING_NAMES = {
    "P01": "Synthetic Alpha Holdings & Co",
    "R03": "Synthétique Trois",
    "R04": "O'Brien Synthetic",
    "R05": "Synthetic Five <Ltd>",
    "R06": "Synthetic Six",
}
RECIPIENTS = [(f"R0{i}", f"Synthetic Recipient {i}") for i in range(1, 7)]
SUB_AGENCIES = [("SA1", "Synthetic Sub-agency One"), ("SA2", "Synthetic Sub-agency Two")]
PSCS = [("PSC1", "Synthetic service & repair"), ("PSC2", "Synthetic product")]
NAICS = [("111111", "Synthetic industry one"), ("222222", "Synthetic industry two")]
COMPETITION = [
    ("FC", "Synthetic code, competed", True),
    ("NC", "Synthetic code, not competed", False),
]

TRANSACTIONS = [
    ("A01", "W01", "2024-10-15", "1000.00", "C", "R01", "P01", "SA1", "PSC1", "111111", "FC", "VA"),
    ("A02", "W01", "2024-12-01", "500.00", "C", "R01", "P01", "SA1", "PSC1", "111111", "FC", "VA"),
    ("A03", "W02", "2025-01-10", "800.00", "D", "R02", "P01", "SA1", "PSC2", "222222", "NC", "MD"),
    ("A04", "W03", "2025-02-20", "700.00", "B", "R03", "R03", "SA2", "PSC1", "111111", "FC", "TX"),
    ("A05", "W03", "2025-03-05", "-200.00", "B", "R03", "R03", "SA2", "PSC1", "111111", "FC", "TX"),
    ("A06", "W04", "2025-05-01", "400.00", "A", "R04", "R04", "SA2", "PSC2", "222222", None, None),
    ("A07", "W05", "2025-07-04", "300.00", "C", "R05", "R05", "SA1", "PSC2", "111111", "NC", "VA"),
    ("A08", "W06", "2025-09-30", "100.00", "D", "R06", "R06", "SA1", "PSC1", "222222", "FC", "MD"),
]


def build_agent_fixture(path: Path, reporting_names: dict[str, str] | None = None) -> Path:
    """Write the synthetic star schema to a new DuckDB file; names can be replaced."""
    names = {**REPORTING_NAMES, **(reporting_names or {})}
    with duckdb.connect(str(path)) as con:
        con.execute(DDL)
        con.execute(DIM_DATE)
        con.executemany("INSERT INTO dim_recipient VALUES (?, ?)", RECIPIENTS)
        con.executemany("INSERT INTO dim_reporting_recipient VALUES (?, ?)", list(names.items()))
        con.executemany("INSERT INTO dim_sub_agency VALUES (?, ?)", SUB_AGENCIES)
        con.executemany("INSERT INTO dim_psc VALUES (?, ?)", PSCS)
        con.executemany("INSERT INTO dim_naics VALUES (?, ?)", NAICS)
        con.executemany("INSERT INTO dim_competition VALUES (?, ?, ?)", COMPETITION)
        con.executemany(
            "INSERT INTO fct_contract_transaction VALUES "
            "(?, ?, CAST(? AS DATE), ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(*row[:3], Decimal(row[3]), *row[4:]) for row in TRANSACTIONS],
        )
    return path
