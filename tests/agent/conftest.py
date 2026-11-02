"""Fixtures for the agent tests: a synthetic star schema and the warehouse of the snapshot.

The warehouse is built once per session from the committed snapshot through the SQLMesh
project, like ``tests/warehouse``, and read through the ADR-0002 query connection. No test
here makes a network call or needs an API key: every model turn comes from a ``ReplayModel``
or a stand-in client.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

# Read by SQLMesh when it is first imported: load models in this process and send no usage
# events (the project config turns them off too).
os.environ.setdefault("MAX_FORK_WORKERS", "1")
os.environ.setdefault("SQLMESH__DISABLE_ANONYMIZED_ANALYTICS", "1")

import duckdb  # noqa: E402
import pytest  # noqa: E402
from agent_fixture import build_agent_fixture  # noqa: E402

from agentic_bi import warehouse  # noqa: E402
from agentic_bi.agent.tools import ToolBox  # noqa: E402
from agentic_bi.semantic import open_query_connection  # noqa: E402


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may reach the live API: the key and the base URL are unset for every test."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("AGENTIC_BI_MODEL", raising=False)


@pytest.fixture(scope="session")
def synthetic_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_agent_fixture(tmp_path_factory.mktemp("agent") / "synthetic.duckdb")


@pytest.fixture
def synthetic_conn(synthetic_db: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    connection = open_query_connection(synthetic_db)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def synthetic_toolbox(synthetic_conn: duckdb.DuckDBPyConnection) -> ToolBox:
    return ToolBox(synthetic_conn)


@pytest.fixture(scope="session")
def warehouse_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    # the file keeps the default name, since SQLMesh's views name the database catalog
    path = tmp_path_factory.mktemp("agent-warehouse") / warehouse.DEFAULT_DATABASE.name
    return warehouse.build(path)


@pytest.fixture(scope="session")
def warehouse_conn(warehouse_db: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    connection = open_query_connection(warehouse_db)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def warehouse_toolbox(warehouse_conn: duckdb.DuckDBPyConnection) -> ToolBox:
    return ToolBox(warehouse_conn)
