"""Fixtures for the golden reproduction: one warehouse build and one run per session.

The build is the S2 project of ``warehouse/`` through ``agentic_bi.warehouse.build``, into a
temporary DuckDB file. Every mapped query then runs through the compiler on the read-only
query connection of ADR-0002 point 6, as ``python -m agentic_bi.reproduce`` runs them.
"""

from __future__ import annotations

import os
from pathlib import Path

# Read by SQLMesh when it is first imported: no fork while DuckDB threads run, no usage events.
os.environ.setdefault("MAX_FORK_WORKERS", "1")
os.environ.setdefault("SQLMESH__DISABLE_ANONYMIZED_ANALYTICS", "1")

import duckdb  # noqa: E402
import pytest  # noqa: E402

from agentic_bi import warehouse  # noqa: E402
from agentic_bi.reproduce import (  # noqa: E402
    Golden,
    GoldenMapping,
    Outcome,
    load_golden,
    load_mapping,
    reproduce,
)
from agentic_bi.semantic import open_query_connection  # noqa: E402
from agentic_bi.semantic.runner import QUERY_CONNECTION_CONFIG  # noqa: E402


@pytest.fixture(scope="session")
def golden() -> Golden:
    return load_golden()


@pytest.fixture(scope="session")
def mapping(golden: Golden) -> GoldenMapping:
    return load_mapping(golden=golden)


@pytest.fixture(scope="session")
def reproduction_database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    # the file keeps the default name, since SQLMesh's views name the database catalog
    path = tmp_path_factory.mktemp("reproduce") / warehouse.DEFAULT_DATABASE.name
    return warehouse.build(path)


@pytest.fixture(scope="session")
def query_connection(reproduction_database: Path):
    connection = open_query_connection(reproduction_database)
    yield connection
    connection.close()


@pytest.fixture(scope="session")
def outcomes(query_connection, mapping: GoldenMapping, golden: Golden) -> dict[str, Outcome]:
    return {outcome.id: outcome for outcome in reproduce(query_connection, mapping, golden)}


@pytest.fixture(scope="session")
def plain_connection(reproduction_database: Path):
    """Read-only access to the built tables for checks the catalog cannot express.

    Used only to prove the premises of a mapping (which code a name has, which
    transactions a UEI selects); no golden value is ever read through it.
    """
    connection = duckdb.connect(
        str(reproduction_database), read_only=True, config=dict(QUERY_CONNECTION_CONFIG)
    )
    yield connection
    connection.close()
