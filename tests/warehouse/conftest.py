"""Fixtures for the warehouse tests: one build of the committed snapshot per session.

The build runs the SQLMesh project of ``warehouse/`` into a temporary DuckDB file, with
every audit blocking. The connection the tests read through is the one ADR-0002 point 6
prescribes for queries: read-only, no external access, configuration locked.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# Read by SQLMesh when it is first imported, so set before the test modules import it:
# load models in this process (no fork while DuckDB threads run), and send no usage
# events (the project config turns them off too).
os.environ.setdefault("MAX_FORK_WORKERS", "1")
os.environ.setdefault("SQLMESH__DISABLE_ANONYMIZED_ANALYTICS", "1")

import duckdb  # noqa: E402
import pytest  # noqa: E402

from agentic_bi import warehouse  # noqa: E402

ROOT = warehouse.ROOT
MANIFEST = ROOT / "data" / "snapshot" / "manifest.json"
GOLDEN = ROOT / "docs" / "golden" / "golden.json"


def query_connection(path: Path) -> duckdb.DuckDBPyConnection:
    """A read-only connection with external access off, as the analyst's queries get."""
    return duckdb.connect(
        str(path),
        read_only=True,
        config={"enable_external_access": False, "lock_configuration": True},
    )


@pytest.fixture(scope="session")
def built_database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    # the file keeps the default name, since SQLMesh's views name the database catalog
    path = tmp_path_factory.mktemp("warehouse") / warehouse.DEFAULT_DATABASE.name
    return warehouse.build(path)


@pytest.fixture(scope="session")
def con(built_database: Path):
    connection = query_connection(built_database)
    yield connection
    connection.close()


@pytest.fixture(scope="session")
def source():
    """The committed snapshot itself, read directly, to compare the models against."""
    connection = duckdb.connect()
    connection.execute(
        "create table snapshot as select * from read_parquet(?)", [str(warehouse.SNAPSHOT_PARQUET)]
    )
    yield connection
    connection.close()


@pytest.fixture(scope="session")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def golden_values() -> dict[str, dict]:
    values = json.loads(GOLDEN.read_text(encoding="utf-8"))["values"]
    return {value["id"]: value for value in values}
