"""The star-schema contract as data: the only table and column names the compiler may emit.

Mirrors ``docs/contracts/star-schema.md``; a test parses that file and fails when the two
differ. The catalog loader checks every column it references against this allow-list, so a
name outside the contract can never reach SQL text.
"""

from __future__ import annotations

FACT_TABLE = "fct_contract_transaction"

TABLES: dict[str, tuple[str, ...]] = {
    "fct_contract_transaction": (
        "transaction_key",
        "award_key",
        "action_date",
        "obligation_amount",
        "award_type_code",
        "recipient_uei",
        "sub_agency_code",
        "psc_code",
        "naics_code",
        "competition_code",
        "pop_state_code",
    ),
    "dim_date": (
        "date",
        "fiscal_year",
        "fiscal_quarter",
        "fiscal_month",
        "calendar_year",
        "calendar_month",
    ),
    "dim_recipient": (
        "recipient_uei",
        "recipient_name",
        "parent_uei",
        "parent_name",
        "reporting_uei",
    ),
    "dim_sub_agency": ("sub_agency_code", "sub_agency_name"),
    "dim_psc": ("psc_code", "psc_description"),
    "dim_naics": ("naics_code", "naics_description"),
    "dim_competition": ("competition_code", "competition_description", "is_competed"),
}

# Each dimension joins many-to-one from the fact: (dimension key, fact column).
JOINS: dict[str, tuple[str, str]] = {
    "dim_date": ("date", "action_date"),
    "dim_recipient": ("recipient_uei", "recipient_uei"),
    "dim_sub_agency": ("sub_agency_code", "sub_agency_code"),
    "dim_psc": ("psc_code", "psc_code"),
    "dim_naics": ("naics_code", "naics_code"),
    "dim_competition": ("competition_code", "competition_code"),
}

# Short aliases used in compiled SQL, one per table.
ALIASES: dict[str, str] = {
    "fct_contract_transaction": "f",
    "dim_date": "d",
    "dim_recipient": "r",
    "dim_sub_agency": "sa",
    "dim_psc": "p",
    "dim_naics": "n",
    "dim_competition": "c",
}

# The fact column the time range applies to.
TIME_COLUMN = "action_date"


def has_column(table: str, column: str) -> bool:
    """Whether ``table.column`` is in the contract."""
    return column in TABLES.get(table, ())
