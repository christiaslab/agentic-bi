"""A small synthetic snapshot with the committed snapshot's schema, for the rule and audit tests.

The rows are invented for the tests and never leave them; no figure here claims to
come from the source. They carry the cases the committed snapshot lacks: a transaction
with no parent UEI, a name that changes, two transactions on one day, a state code
abroad and a missing competition code. ``build`` runs the real project over them.
"""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb
from sqlmesh.utils.errors import NodeAuditsErrors

from agentic_bi import warehouse
from agentic_bi.ingest import spec

COMMON = {
    "award_id_piid": "PIID",
    "modification_number": "0",
    "action_date_fiscal_year": 2025,
    "awarding_agency_code": "068",
    "awarding_sub_agency_code": "6800",
    "awarding_sub_agency_name": "Environmental Protection Agency",
    "award_type_code": "C",
    "award_type": "DELIVERY ORDER",
    "product_or_service_code": "F108",
    "product_or_service_code_description": "PSC LABEL",
    "naics_code": "562910",
    "naics_description": "NAICS LABEL",
}

COMPETED = ("A", "FULL AND OPEN COMPETITION")

ROWS = [
    # R1 changes name; its parent P1 changes name; T02 and T03 share P1's latest day
    dict(key="T01", award="AW1", amount="100.00", day="2024-10-05", uei="R1", name="ALPHA OLD",
         parent="P1", parent_name="PARENT ONE OLD"),
    dict(key="T02", award="AW1", amount="-25.50", day="2025-03-01", uei="R1", name="ALPHA NEW",
         parent="P1", parent_name="PARENT ONE A", competed=("B", "NOT AVAILABLE FOR COMPETITION")),
    dict(key="T03", award="AW2", amount="10.00", day="2025-03-01", uei="R2", name="BRAVO",
         parent="P1", parent_name="PARENT ONE B"),
    # no parent recorded: the recipient reports for itself; abroad; no competition code
    dict(key="T04", award="AW3", amount="5.25", day="2025-09-30", uei="R3", name="CHARLIE",
         parent=None, parent_name=None, country="CAN", state="ON", competed=(None, None)),
    # R4 changes parent during the year
    dict(key="T05", award="AW4", amount="7.00", day="2024-11-15", uei="R4", name="DELTA",
         parent="P2", parent_name="PARENT TWO"),
    dict(key="T06", award="AW4", amount="-1.00", day="2025-05-20", uei="R4", name="DELTA",
         parent="P3", parent_name="PARENT THREE"),
]  # fmt: skip


def _record(row: dict) -> dict:
    code, label = row.get("competed", COMPETED)
    record = dict(COMMON)
    record.update(
        contract_transaction_unique_key=row["key"],
        contract_award_unique_key=row["award"],
        federal_action_obligation=row["amount"],
        action_date=row["day"],
        action_date_fiscal_year=row.get("fiscal_year", COMMON["action_date_fiscal_year"]),
        recipient_uei=row["uei"],
        recipient_name=row["name"],
        recipient_parent_uei=row["parent"],
        recipient_parent_name=row["parent_name"],
        primary_place_of_performance_country_code=row.get("country", "USA"),
        primary_place_of_performance_state_code=row.get("state", "CA"),
        extent_competed_code=code,
        extent_competed=label,
    )
    return record


def _type_of(field: str) -> str:
    if field in spec.DECIMAL_FIELDS:
        return "DECIMAL(18,2)"
    if field in spec.DATE_FIELDS:
        return "DATE"
    if field in spec.INTEGER_FIELDS:
        return "INTEGER"
    return "VARCHAR"


def write_snapshot(path: Path, rows: list[dict]) -> Path:
    """A Parquet file with the fields and types of the committed snapshot, holding ``rows``."""
    con = duckdb.connect()
    try:
        columns = ", ".join(f"{field} {_type_of(field)}" for field in spec.FIELDS)
        con.execute(f"create table snapshot ({columns})")
        placeholders = ", ".join("?" for _ in spec.FIELDS)
        con.executemany(
            f"insert into snapshot values ({placeholders})",
            [[_record(row)[field] for field in spec.FIELDS] for row in rows],
        )
        literal = str(path).replace("'", "''")  # COPY takes no bound parameter
        con.execute(f"copy snapshot to '{literal}' (format parquet)")
    finally:
        con.close()
    return path


def build(directory: Path, rows: list[dict]) -> Path:
    """Build the project over ``rows`` into a database in ``directory``."""
    snapshot = write_snapshot(directory / "synthetic.parquet", rows)
    return warehouse.build(directory / warehouse.DEFAULT_DATABASE.name, snapshot)


def failed_audits(records: list[logging.LogRecord]) -> set[tuple[str, str, str]]:
    """(model, audit, column) of every audit SQLMesh logged as failed."""
    found = set()
    for record in records:
        error = record.exc_info[1] if record.exc_info else None
        while error is not None:
            if isinstance(error, NodeAuditsErrors):
                for audit in error.errors:
                    args = audit.audit_args
                    column = args.get("column", args.get("columns"))
                    found.add((audit.model_name, audit.audit_name, column.sql() if column else ""))
            error = error.__cause__ or error.__context__
    return found
