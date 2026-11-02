"""The Parquet export: contract tables, contract types, snapshot totals, committed files."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

import duckdb
import pytest

from agentic_bi.powerbi import ROOT, PowerBIError, export
from agentic_bi.powerbi.schema import load_schema

COMMITTED_DATA = ROOT / "data" / "model"

SCHEMA = load_schema()


def _describe(path) -> list[tuple[str, str]]:
    connection = duckdb.connect()
    try:
        rows = connection.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]).fetchall()
    finally:
        connection.close()
    return [(row[0], row[1].replace(", ", ",")) for row in rows]


def _rows(path) -> list[tuple]:
    connection = duckdb.connect()
    try:
        return connection.execute("SELECT * FROM read_parquet(?)", [str(path)]).fetchall()
    finally:
        connection.close()


def test_one_file_per_contract_table(exported) -> None:
    assert [item.table for item in exported.files] == [table.name for table in SCHEMA.tables]
    for item in exported.files:
        assert item.path.name == f"{item.table}.parquet"
        assert item.path.parent == exported.manifest.parent


@pytest.mark.parametrize("table", SCHEMA.tables, ids=lambda table: table.name)
def test_each_file_has_the_contract_columns_and_types(exported, table) -> None:
    path = dict((item.table, item.path) for item in exported.files)[table.name]
    assert _describe(path) == [(column.name, column.type) for column in table.columns]


def test_the_fact_equals_the_snapshot_manifest(exported, snapshot_manifest) -> None:
    assert exported.fact_rows == snapshot_manifest["row_count"] == 12687
    assert exported.fact_obligation_sum == Decimal(snapshot_manifest["obligation_sum"])


def test_rows_are_sorted_by_the_key_and_the_key_is_unique(exported) -> None:
    for item in exported.files:
        table = SCHEMA.table(item.table)
        key = table.key or "transaction_key"
        position = [column.name for column in table.columns].index(key)
        keys = [row[position] for row in _rows(item.path)]
        assert keys == sorted(keys), item.table
        assert len(set(keys)) == len(keys), item.table


def test_a_mismatch_with_the_snapshot_stops_the_export(exported, snapshot_manifest) -> None:
    fact = exported.files[0].path
    wrong_rows = dict(snapshot_manifest, row_count=snapshot_manifest["row_count"] + 1)
    with pytest.raises(PowerBIError, match="snapshot manifest"):
        export.check_against_snapshot(fact, wrong_rows)
    wrong_sum = dict(snapshot_manifest, obligation_sum="1697318332.64")
    with pytest.raises(PowerBIError, match="snapshot manifest"):
        export.check_against_snapshot(fact, wrong_sum)


def test_the_same_database_gives_the_same_bytes(exported, tmp_path) -> None:
    database = exported.manifest.parent.parent / "build" / "warehouse.duckdb"
    again = export.write_parquet(database, tmp_path / "again")
    for item, (table, path) in zip(exported.files, again, strict=True):
        assert table == item.table
        assert path.read_bytes() == item.path.read_bytes(), table


def test_the_committed_files_hold_what_a_fresh_export_holds(exported) -> None:
    for item in exported.files:
        committed = COMMITTED_DATA / item.path.name
        assert _describe(committed) == _describe(item.path), item.table
        assert _rows(committed) == _rows(item.path), item.table


def test_the_committed_manifest_describes_the_committed_files(snapshot_manifest) -> None:
    manifest = json.loads((COMMITTED_DATA / export.MANIFEST_NAME).read_text(encoding="utf-8"))
    listed = {entry["path"] for entry in manifest["files"]}
    on_disk = {path.name for path in COMMITTED_DATA.glob("*.parquet")}
    assert listed == on_disk == {f"{table.name}.parquet" for table in SCHEMA.tables}
    for entry in manifest["files"]:
        path = COMMITTED_DATA / entry["path"]
        assert entry["bytes"] == path.stat().st_size
        assert entry["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert entry["rows"] == len(_rows(path))
    assert manifest["total_bytes"] == sum(entry["bytes"] for entry in manifest["files"])
    assert manifest["fact"]["row_count"] == snapshot_manifest["row_count"]
    assert manifest["fact"]["obligation_sum"] == snapshot_manifest["obligation_sum"]


def test_the_committed_files_stay_under_ten_megabytes() -> None:
    total = sum(path.stat().st_size for path in COMMITTED_DATA.iterdir() if path.is_file())
    assert total < export.COMMIT_LIMIT_BYTES == 10_000_000


def test_the_command_refuses_a_folder_outside_the_repository(tmp_path, capsys) -> None:
    assert not tmp_path.is_relative_to(ROOT)
    assert export.main(["--out", str(tmp_path / "model")]) == 1
    assert "outside" in capsys.readouterr().err
    assert not (tmp_path / "model").exists()


def test_the_command_refuses_a_dot_dot_escape(capsys) -> None:
    assert export.main(["--out", str(ROOT / "data" / ".." / ".." / "escape")]) == 1
    assert "outside" in capsys.readouterr().err


def test_a_failed_check_leaves_the_output_folder_untouched(tmp_path) -> None:
    database = tmp_path / "build" / "warehouse.duckdb"
    wrong = tmp_path / "manifest.json"
    wrong.write_text(json.dumps({"row_count": 1, "obligation_sum": "0.00"}), encoding="utf-8")
    out = tmp_path / "model"
    out.mkdir()
    (out / "old.txt").write_text("old", encoding="utf-8")
    with pytest.raises(PowerBIError, match="snapshot manifest"):
        export.export(out=out, database=database, snapshot_manifest=wrong)
    assert sorted(p.name for p in out.iterdir()) == ["old.txt"]
    assert not [p for p in database.parent.iterdir() if p.name.startswith("export-")]


@pytest.mark.parametrize("table", SCHEMA.tables, ids=lambda table: table.name)
def test_text_keys_stay_distinct_without_case_or_trailing_spaces(table) -> None:
    # Power BI compares text without case and joins keys without trailing spaces; DuckDB does
    # neither. Keys that merged that way would make the DAX and the SQL count differently.
    path = COMMITTED_DATA / f"{table.name}.parquet"
    keys = [table.key] if table.key else ["transaction_key", "award_key", "reporting_uei"]
    connection = duckdb.connect()
    try:
        for key in keys:
            if table.column(key).type != "VARCHAR":
                continue
            exact, folded = connection.execute(
                f'SELECT count(DISTINCT "{key}"), count(DISTINCT lower(rtrim("{key}"))) '
                "FROM read_parquet(?)",
                [str(path)],
            ).fetchone()
            assert exact == folded, (table.name, key)
    finally:
        connection.close()
