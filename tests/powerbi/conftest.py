"""Fixtures for the Power BI tests: the committed project and data, and one export per session.

The export builds the warehouse into a temporary DuckDB file and writes the Parquet files
next to it, as ``python -m agentic_bi.powerbi.export`` does into ``data/model/``.
"""

from __future__ import annotations

import json
import os

# Read by SQLMesh when it is first imported: load models in this process and send no usage
# events, as the warehouse tests do.
os.environ.setdefault("MAX_FORK_WORKERS", "1")
os.environ.setdefault("SQLMESH__DISABLE_ANONYMIZED_ANALYTICS", "1")

import pytest  # noqa: E402

from agentic_bi.powerbi import ROOT  # noqa: E402

GOLDEN = ROOT / "docs" / "golden" / "golden.json"
SNAPSHOT_MANIFEST = ROOT / "data" / "snapshot" / "manifest.json"


@pytest.fixture(scope="session")
def exported(tmp_path_factory: pytest.TempPathFactory):
    from agentic_bi.powerbi.export import export

    base = tmp_path_factory.mktemp("powerbi_export")
    # the file keeps the default name, since SQLMesh's views name the database catalog
    return export(out=base / "model", database=base / "build" / "warehouse.duckdb")


@pytest.fixture(scope="session")
def snapshot_manifest() -> dict:
    return json.loads(SNAPSHOT_MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def golden_values() -> dict[str, dict]:
    values = json.loads(GOLDEN.read_text(encoding="utf-8"))["values"]
    return {value["id"]: value for value in values}
