"""The query connection: read-only, no external access, locked, with a row limit and a timeout."""

import time
from pathlib import Path

import duckdb
import pytest

from agentic_bi.semantic import runner
from agentic_bi.semantic.errors import QueryTimeoutError, RowLimitExceededError, SemanticError


def test_query_connection_settings(conn) -> None:
    settings = conn.execute(
        "SELECT current_setting('enable_external_access'), current_setting('lock_configuration'),"
        " current_setting('autoload_known_extensions'),"
        " current_setting('autoinstall_known_extensions')"
    ).fetchone()
    assert settings == (False, True, False, False)
    readonly = conn.execute(
        "SELECT readonly FROM duckdb_databases() WHERE database_name = current_database()"
    ).fetchone()
    assert readonly == (True,)


CATALOG_FILE = Path(__file__).resolve().parents[2] / "catalog" / "metrics.yaml"


@pytest.mark.parametrize("function", ["read_text", "read_csv", "read_parquet", "read_blob", "glob"])
def test_file_reading_table_function_is_refused(conn, tmp_path, function: str) -> None:
    # Any file other than the open database's own: a fresh CSV, a project file, a system file.
    other = tmp_path / "other.csv"
    other.write_text("a,b\n1,2\n", encoding="utf-8")
    for path in (other, CATALOG_FILE, Path("/etc/hosts")):
        with pytest.raises(duckdb.PermissionException, match="disabled by configuration"):
            conn.execute(f"SELECT * FROM {function}(?)", [str(path)]).fetchall()


@pytest.mark.parametrize(
    "statement",
    [
        "COPY (SELECT 1) TO 'out.csv'",
        "SELECT 1; SELECT 2",
        "SET threads = 1",
        "CALL pragma_version()",
    ],
)
def test_runner_runs_only_a_single_select(conn, statement: str) -> None:
    with pytest.raises(SemanticError, match="single SELECT"):
        runner._run(conn, statement, [], row_limit=10, timeout_seconds=5)


def test_configuration_cannot_be_unlocked(conn) -> None:
    for statement in ("SET enable_external_access = true", "SET lock_configuration = false"):
        with pytest.raises(duckdb.InvalidInputException, match="locked"):
            conn.execute(statement)


def test_writes_are_refused(conn) -> None:
    with pytest.raises(duckdb.Error, match="read-only"):
        conn.execute("DELETE FROM fct_contract_transaction")
    assert conn.execute("SELECT count(*) FROM fct_contract_transaction").fetchone() == (16,)


def test_execute_refuses_a_connection_that_is_not_locked_down() -> None:
    with duckdb.connect() as writable:
        with pytest.raises(SemanticError, match="not a query connection"):
            runner.execute(writable, {"metric": "obligations"})


def test_execute_returns_named_columns_and_the_compiled_query(conn) -> None:
    result = runner.execute(conn, {"metric": "award_count", "dimensions": ["award_type"]})
    assert result.columns == result.compiled.columns == ("award_type", "award_count")
    # Every date held, so W14 (type C, fiscal year 2024) counts too.
    assert result.records() == [
        {"award_type": "A", "award_count": 2},
        {"award_type": "B", "award_count": 2},
        {"award_type": "C", "award_count": 7},
        {"award_type": "D", "award_count": 3},
    ]


def test_row_limit_is_enforced(conn) -> None:
    query = {"metric": "obligations", "grain": "fiscal_month"}  # 13 rows: 12 + one in FY2024
    assert len(runner.execute(conn, query, row_limit=13).rows) == 13
    with pytest.raises(RowLimitExceededError):
        runner.execute(conn, query, row_limit=12)


def test_timeout_interrupts_a_long_query_and_the_connection_survives(conn) -> None:
    # The compiled metrics finish in milliseconds on the fixture, so the timer is tested on
    # a deliberately slow statement through the runner's private helper.
    slow = "SELECT count(*) FROM range(10000000000000) AS r(x) WHERE x % 7 = 3"
    started = time.monotonic()
    with pytest.raises(QueryTimeoutError):
        runner._run(conn, slow, [], row_limit=1, timeout_seconds=0.2)
    assert time.monotonic() - started < 10
    assert runner.execute(conn, {"metric": "award_count"}).rows == [(14,)]


def test_open_query_connection_needs_an_existing_file(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        runner.open_query_connection(tmp_path / "absent.duckdb")
