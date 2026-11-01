"""The audits are wired to every key of the fact, and a broken key fails the build.

The broken cases are builds of the synthetic snapshot with one defect class each; the
session build of the committed snapshot stays untouched.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import synthetic_snapshot
from sqlmesh import Context
from sqlmesh.utils.errors import PlanError

from agentic_bi import warehouse

FACT = "main.fct_contract_transaction"
COMPETITION = "main.dim_competition"

# fact column -> (dimension, key), as docs/contracts/star-schema.md joins them
FOREIGN_KEYS = {
    "action_date": ("main.dim_date", "date"),
    "recipient_uei": ("main.dim_recipient", "recipient_uei"),
    "reporting_uei": ("main.dim_reporting_recipient", "reporting_uei"),
    "sub_agency_code": ("main.dim_sub_agency", "sub_agency_code"),
    "psc_code": ("main.dim_psc", "psc_code"),
    "naics_code": ("main.dim_naics", "naics_code"),
    "competition_code": ("main.dim_competition", "competition_code"),
}


def _declared_audits(database: Path, model_name: str) -> list[tuple[str, dict]]:
    config = warehouse.project_config(database)
    context = Context(paths=str(warehouse.PROJECT_DIR), config=config)
    try:
        return [
            (audit.name, args) for audit, args in context.get_model(model_name).audits_with_args
        ]
    finally:
        context.close()


def _failing_build(tmp_path: Path, rows: list[dict], caplog) -> set[tuple[str, str, str]]:
    with caplog.at_level(logging.INFO, logger="sqlmesh"), pytest.raises(PlanError):
        synthetic_snapshot.build(tmp_path, rows)
    return synthetic_snapshot.failed_audits(caplog.records)


def test_the_fact_declares_its_key_and_foreign_key_audits(built_database: Path) -> None:
    declared = _declared_audits(built_database, FACT)
    columns = {name: args["columns"].sql() for name, args in declared if "columns" in args}
    assert columns["not_null"] == columns["unique_values"] == "(transaction_key)"
    foreign = [
        (args["column"].name, (args["dimension"].sql(), args["key"].name))
        for name, args in declared
        if name == "foreign_key"
    ]
    assert dict(foreign) == FOREIGN_KEYS
    assert len(foreign) == len(FOREIGN_KEYS)


def test_null_foreign_keys_are_the_source_nulls(con, source) -> None:
    source_fields = {
        "action_date": "action_date",
        "recipient_uei": "recipient_uei",
        "reporting_uei": "coalesce(recipient_parent_uei, recipient_uei)",
        "sub_agency_code": "awarding_sub_agency_code",
        "psc_code": "product_or_service_code",
        "naics_code": "naics_code",
        "competition_code": "extent_competed_code",
    }
    for column, field in source_fields.items():
        built = con.execute(
            f"select count(*) from fct_contract_transaction where {column} is null"
        ).fetchone()[0]
        expected = source.execute(f"select count(*) from snapshot where {field} is null").fetchone()
        assert built == expected[0], column


def test_a_bad_transaction_key_or_an_out_of_year_date_fails_the_fact(tmp_path, caplog) -> None:
    rows = synthetic_snapshot.ROWS + [
        # a second T01, a transaction with no key, and a date of the next fiscal year
        dict(synthetic_snapshot.ROWS[0], amount="1.00"),
        dict(synthetic_snapshot.ROWS[2], key=None),
        dict(synthetic_snapshot.ROWS[4], key="T09", day="2025-10-01", fiscal_year=2026),
    ]
    failed = _failing_build(tmp_path, rows, caplog)
    assert failed == {
        (FACT, "not_null", "(transaction_key)"),
        (FACT, "unique_values", "(transaction_key)"),
        (FACT, "foreign_key", "action_date"),
    }


def test_a_code_missing_from_the_seed_or_relabelled_fails_the_competition_audits(
    tmp_path, caplog
) -> None:
    rows = synthetic_snapshot.ROWS + [
        dict(synthetic_snapshot.ROWS[0], key="T07", competed=("E", "FOLLOW ON TO COMPETED ACTION")),
        dict(synthetic_snapshot.ROWS[0], key="T08", competed=("F", "COMPETED")),
    ]
    failed = _failing_build(tmp_path, rows, caplog)
    assert failed == {
        (COMPETITION, "not_null", "(competition_code, competition_description, is_competed)"),
        (COMPETITION, "competition_seed_matches_source", ""),
    }
