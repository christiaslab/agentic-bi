"""The committed snapshot and golden values: integrity, allow-list, same day, reproduction.

These tests read data/snapshot/ and docs/golden/golden.json. They skip only when the
Parquet is not committed (ADR-0001: over about 10 MB it is fetched in CI instead).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from agentic_bi.ingest import fetch, golden, spec

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "data" / "snapshot"
GOLDEN = ROOT / "docs" / "golden" / golden.GOLDEN_NAME
PARQUET = SNAPSHOT / fetch.PARQUET_NAME

pytestmark = pytest.mark.skipif(not PARQUET.is_file(), reason="snapshot Parquet not committed")

# owner_api dimension -> snapshot column, for reproducing the publisher's figures
DIMENSION_COLUMNS = {
    "recipient_uei": "recipient_uei",
    "psc_code": "product_or_service_code",
    "naics_code": "naics_code",
    "pop_state_code": "primary_place_of_performance_state_code",
    "sub_agency_name": "awarding_sub_agency_name",
}


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((SNAPSHOT / fetch.MANIFEST_NAME).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def golden_file() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def con():
    connection = duckdb.connect()
    connection.execute("create table snapshot as select * from read_parquet(?)", [str(PARQUET)])
    yield connection
    connection.close()


def test_parquet_matches_the_manifest(manifest: dict) -> None:
    (entry,) = manifest["files"]
    assert entry["path"] == fetch.PARQUET_NAME
    assert entry["bytes"] == PARQUET.stat().st_size < fetch.SIZE_LIMIT_BYTES
    assert entry["sha256"] == fetch.sha256_file(PARQUET)
    assert manifest["filters"] == spec.filters()
    assert manifest["fields"] == list(spec.FIELDS)


def test_snapshot_schema_holds_no_excluded_column(con) -> None:
    names = [row[0] for row in con.execute("describe snapshot").fetchall()]
    assert spec.excluded_in(names) == []
    assert names == list(spec.FIELDS)


def test_snapshot_and_golden_share_one_capture_day(manifest: dict, golden_file: dict) -> None:
    day = manifest["capture_date_utc"]
    assert golden_file["capture_date_utc"] == golden_file["snapshot"]["capture_date_utc"] == day
    assert golden_file["snapshot"]["sha256"] == manifest["files"][0]["sha256"]
    assert {value["capture_date"] for value in golden_file["values"]} == {day}
    assert 30 <= len(golden_file["values"]) <= 50


def test_row_count_and_obligations_reconcile_to_the_cent(
    con, manifest: dict, golden_file: dict
) -> None:
    rows, total = con.execute(
        "select count(*), sum(federal_action_obligation) from snapshot"
    ).fetchone()
    values = {value["id"]: value for value in golden_file["values"]}
    assert rows == manifest["row_count"] == values["transactions.count"]["value"]
    assert Decimal(total) == Decimal(manifest["obligation_sum"])
    assert Decimal(total) == Decimal(values["obligations.total"]["value"])
    assert golden_file["reconciliation"]["status"] == "match"
    assert all(
        check["difference"] in {"0", "0.00"} for check in golden_file["reconciliation"]["checks"]
    )


def _reproduce(con, value: dict) -> Decimal | int:
    dims = value["dimensions"]
    if value["id"] == "transactions.count":
        return con.execute("select count(*) from snapshot").fetchone()[0]
    if "fiscal_month" in dims:
        calendar_month = (dims["fiscal_month"] + 8) % 12 + 1  # fiscal month 1 is October
        sql = "select sum(federal_action_obligation) from snapshot where month(action_date) = ?"
        return con.execute(sql, [calendar_month]).fetchone()[0]
    matched = [name for name in DIMENSION_COLUMNS if name in dims]
    if not matched:
        return con.execute("select sum(federal_action_obligation) from snapshot").fetchone()[0]
    column = DIMENSION_COLUMNS[matched[0]]
    sql = f"select sum(federal_action_obligation) from snapshot where {column} = ?"
    return con.execute(sql, [dims[matched[0]]]).fetchone()[0]


def test_every_owner_value_reproduces_from_the_snapshot(con, golden_file: dict) -> None:
    owner = [value for value in golden_file["values"] if value["tag"] == "owner_api"]
    assert owner
    for value in owner:
        assert f"{_reproduce(con, value)}" == f"{value['value']}", value["id"]


def test_every_derived_value_reruns_from_its_recorded_sql(con, golden_file: dict) -> None:
    derived = [value for value in golden_file["values"] if value["tag"] == "derived"]
    assert derived
    for value in derived:
        result = con.execute(value["derivation"]["sql"]).fetchall()
        assert result, value["id"]
    rerun = golden.derived_values(con, {}, golden_file["capture_date_utc"])
    assert [(v["id"], v["value"]) for v in rerun] == [(v["id"], v["value"]) for v in derived]
