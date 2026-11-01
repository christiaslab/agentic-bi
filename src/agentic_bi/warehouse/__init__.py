"""The SQLMesh project that builds the star schema (S2), and how to point it at a database.

``warehouse/config.py`` calls :func:`project_config` with no argument, so
``uv run sqlmesh -p warehouse plan --auto-apply --no-prompts`` builds into
``data/tmp/warehouse.duckdb``. Tests and later sessions call :func:`build` with a
database path of their own. The models read the committed snapshot only.
"""

from __future__ import annotations

from pathlib import Path

from agentic_bi.ingest import spec

ROOT = Path(__file__).resolve().parents[3]
PROJECT_DIR = ROOT / "warehouse"
SNAPSHOT_PARQUET = ROOT / "data" / "snapshot" / "contract_transactions.parquet"
DEFAULT_DATABASE = ROOT / "data" / "tmp" / "warehouse.duckdb"

# The schema that holds exactly the tables of docs/contracts/star-schema.md.
CONTRACT_SCHEMA = "main"
GATEWAY = "local"


def project_config(database: Path | str | None = None, snapshot_parquet: Path | str | None = None):
    """The SQLMesh configuration of the project, building into ``database``.

    ``snapshot_parquet`` defaults to the committed snapshot; tests of the model rules
    pass a synthetic file of the same schema. The parent directory of the database is
    created when missing, since DuckDB does not create it.
    """
    from sqlmesh.core.config import (
        Config,
        DuckDBConnectionConfig,
        GatewayConfig,
        ModelDefaultsConfig,
    )

    path = Path(database) if database is not None else DEFAULT_DATABASE
    path.parent.mkdir(parents=True, exist_ok=True)
    return Config(
        gateways={GATEWAY: GatewayConfig(connection=DuckDBConnectionConfig(database=str(path)))},
        default_gateway=GATEWAY,
        model_defaults=ModelDefaultsConfig(dialect="duckdb", start=spec.START_DATE),
        variables={
            "snapshot_parquet": str(snapshot_parquet or SNAPSHOT_PARQUET),
            "fiscal_year": spec.FISCAL_YEAR,
        },
    )


def build(database: Path | str, snapshot_parquet: Path | str | None = None) -> Path:
    """Plan and apply the project to ``prod`` in ``database``; a failed audit raises."""
    from sqlmesh import Context

    path = Path(database)
    context = Context(paths=str(PROJECT_DIR), config=project_config(path, snapshot_parquet))
    try:
        context.plan(auto_apply=True, no_prompts=True)
    finally:
        context.close()
    return path
