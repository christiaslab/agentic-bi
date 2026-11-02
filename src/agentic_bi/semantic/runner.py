"""Run compiled queries on a locked-down DuckDB connection (ADR-0002 points 5 and 6).

``open_query_connection`` opens the database read-only, with external access disabled and the
configuration locked, so SQL on it cannot reach other files, load an extension or undo those
settings. ``execute`` takes a structured query only: it compiles it and runs the compiler's
output under a row limit and a timeout. There is no raw-SQL entry point.

DuckDB keeps the open database's own files (the database, its ``.wal`` and its temp
directory) on an internal allow-list even with external access disabled, and in DuckDB 1.5.6
a ``COPY ... TO`` that names the ``.wal`` path succeeds on such a connection. The compiler
never emits ``COPY``; as a second line, ``_run`` parses the SQL with DuckDB and runs it only
when it is exactly one SELECT statement.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from .catalog import Catalog
from .compiler import CompiledQuery, compile
from .errors import QueryTimeoutError, RowLimitExceededError, SemanticError
from .query import StructuredQuery

DEFAULT_ROW_LIMIT = 10_000
DEFAULT_TIMEOUT_SECONDS = 30.0

# lock_configuration makes the other settings final for the life of the connection.
QUERY_CONNECTION_CONFIG: Mapping[str, Any] = {
    "enable_external_access": False,
    "autoinstall_known_extensions": False,
    "autoload_known_extensions": False,
    "lock_configuration": True,
}


@dataclass(frozen=True)
class QueryResult:
    compiled: CompiledQuery
    columns: tuple[str, ...]
    rows: list[tuple[Any, ...]]

    def records(self) -> list[dict[str, Any]]:
        """The rows as dictionaries keyed by output column."""
        return [dict(zip(self.columns, row, strict=True)) for row in self.rows]


def open_query_connection(database: str | Path) -> duckdb.DuckDBPyConnection:
    """Open an existing DuckDB file as a query connection."""
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"no DuckDB database at {path}")
    return duckdb.connect(str(path), read_only=True, config=dict(QUERY_CONNECTION_CONFIG))


def check_query_connection(connection: duckdb.DuckDBPyConnection) -> None:
    """Refuse a connection that is not read-only, sandboxed and locked."""
    readonly = connection.execute(
        "SELECT readonly FROM duckdb_databases() WHERE database_name = current_database()"
    ).fetchone()
    settings = connection.execute(
        "SELECT current_setting('enable_external_access'), current_setting('lock_configuration')"
    ).fetchone()
    if readonly != (True,) or settings != (False, True):
        raise SemanticError(
            "not a query connection: open it with open_query_connection (read-only, "
            "enable_external_access=false, lock_configuration=true)"
        )


def execute(
    connection: duckdb.DuckDBPyConnection,
    query: StructuredQuery | Mapping[str, Any],
    *,
    catalog: Catalog | None = None,
    row_limit: int = DEFAULT_ROW_LIMIT,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> QueryResult:
    """Compile a structured query and run it on a query connection."""
    compiled = compile(query, catalog)
    check_query_connection(connection)
    columns, rows = _run(connection, compiled.sql, compiled.params, row_limit, timeout_seconds)
    return QueryResult(compiled=compiled, columns=columns, rows=rows)


def _run(
    connection: duckdb.DuckDBPyConnection,
    sql: str,
    params: list[Any],
    row_limit: int,
    timeout_seconds: float,
) -> tuple[tuple[str, ...], list[tuple[Any, ...]]]:
    if row_limit < 1 or timeout_seconds <= 0:
        raise ValueError("row_limit and timeout_seconds must be positive")
    statements = connection.extract_statements(sql)
    if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
        raise SemanticError("only a single SELECT statement runs on a query connection")
    timer = threading.Timer(timeout_seconds, connection.interrupt)
    timer.start()
    try:
        cursor = connection.execute(sql, params)
        rows = cursor.fetchmany(row_limit + 1)
        columns = tuple(item[0] for item in cursor.description)
    except duckdb.InterruptException as exc:
        raise QueryTimeoutError(f"the query ran longer than {timeout_seconds} s") from exc
    finally:
        timer.cancel()
    if len(rows) > row_limit:
        raise RowLimitExceededError(f"the result has more than {row_limit} rows; narrow the query")
    return columns, rows
