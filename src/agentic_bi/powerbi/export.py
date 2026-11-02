"""Export the contract tables to Parquet for Power BI (ADR-0004 point 3).

``uv run python -m agentic_bi.powerbi.export --out data/model`` builds the warehouse with
``agentic_bi.warehouse.build`` into ``data/tmp/powerbi/`` and writes one Parquet file per
contract table, with the contract's column names, order and types, rows sorted by the
table's key. The files are first written beside the build and the fact's row count and
obligation sum checked against ``data/snapshot/manifest.json``; a difference stops the
export with an error and leaves ``--out`` untouched. Only then are the files moved there.
``manifest.json`` beside the files records each file's rows, bytes and SHA-256, and the
fact's checked totals.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from . import ROOT, PowerBIError, inside
from .schema import StarSchema, load_schema

SNAPSHOT_MANIFEST = ROOT / "data" / "snapshot" / "manifest.json"
DEFAULT_OUT = ROOT / "data" / "model"
BUILD_DATABASE = ROOT / "data" / "tmp" / "powerbi" / "warehouse.duckdb"
MANIFEST_NAME = "manifest.json"
# ADR-0004: the Parquet files are committed only when under 10 MB together.
COMMIT_LIMIT_BYTES = 10_000_000
# Snappy is DuckDB's default codec and the one Parquet readers most widely support.
COMPRESSION = "snappy"
# The contract's amount column, checked against the snapshot manifest.
AMOUNT_COLUMN = "obligation_amount"


@dataclass(frozen=True)
class ExportedFile:
    table: str
    path: Path
    rows: int
    bytes: int
    sha256: str


@dataclass(frozen=True)
class ExportResult:
    files: tuple[ExportedFile, ...]
    fact_rows: int
    fact_obligation_sum: Decimal
    manifest: Path

    @property
    def total_bytes(self) -> int:
        return sum(item.bytes for item in self.files)


def _quote(identifier: str) -> str:
    # Names come from the contract document, already checked against the contract module.
    if not identifier.replace("_", "").isalnum() or not identifier.isascii():
        raise PowerBIError(f"{identifier!r} is not a plain identifier")
    return f'"{identifier}"'


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_parquet(database: Path | str, out: Path | str, schema: StarSchema | None = None):
    """Write one Parquet file per contract table of ``database`` into ``out``.

    Returns ``(table, path)`` pairs in contract order. Each file holds the contract's
    columns in contract order, sorted by the table's key (``transaction_key`` for the fact),
    so the same database gives byte-identical files.
    """
    import duckdb

    schema = schema or load_schema()
    target = Path(out)
    target.mkdir(parents=True, exist_ok=True)
    written = []
    connection = duckdb.connect(str(database), read_only=True)
    try:
        # One thread: the writer then produces the same bytes on every run.
        connection.execute("SET threads = 1")
        for table in schema.tables:
            columns = ", ".join(_quote(column.name) for column in table.columns)
            key = table.key or table.columns[0].name
            relation = connection.sql(
                f"SELECT {columns} FROM {_quote(table.name)} ORDER BY {_quote(key)}"
            )
            path = target / f"{table.name}.parquet"
            # The path goes to DuckDB as an argument, never into SQL text.
            relation.write_parquet(str(path), compression=COMPRESSION)
            written.append((table.name, path))
    finally:
        connection.close()
    return written


def check_against_snapshot(fact_parquet: Path, snapshot_manifest: dict) -> tuple[int, Decimal]:
    """Row count and obligation sum of the fact file; raise unless they equal the snapshot."""
    import duckdb

    connection = duckdb.connect()
    try:
        rows, total = connection.execute(
            f"SELECT count(*), sum({_quote(AMOUNT_COLUMN)}) FROM read_parquet(?)",
            [str(fact_parquet)],
        ).fetchone()
    finally:
        connection.close()
    expected_rows = int(snapshot_manifest["row_count"])
    expected_sum = Decimal(snapshot_manifest["obligation_sum"])
    total = Decimal(total) if total is not None else None
    if rows != expected_rows or total != expected_sum:
        raise PowerBIError(
            f"the exported fact holds {rows} rows summing to {total}; the snapshot manifest "
            f"says {expected_rows} rows summing to {expected_sum}"
        )
    return rows, total


def export(
    out: Path | str = DEFAULT_OUT,
    database: Path | str = BUILD_DATABASE,
    snapshot_parquet: Path | str | None = None,
    snapshot_manifest: Path | str = SNAPSHOT_MANIFEST,
) -> ExportResult:
    """Build the warehouse into ``database`` and export its contract tables to ``out``."""
    from agentic_bi import warehouse

    manifest = json.loads(Path(snapshot_manifest).read_text(encoding="utf-8"))
    schema = load_schema()
    built = warehouse.build(database, snapshot_parquet)
    target = Path(out)
    # Write beside the build and check there first: files that do not reconcile with the
    # snapshot never reach ``out`` (AGENTS.md, never invent data).
    staging = Path(tempfile.mkdtemp(prefix="export-", dir=Path(built).parent))
    try:
        staged = write_parquet(built, staging, schema)
        rows, total = check_against_snapshot(dict(staged)[schema.fact], manifest)
        target.mkdir(parents=True, exist_ok=True)
        written = []
        for table, path in staged:
            destination = target / path.name
            shutil.move(path, destination)
            written.append((table, destination))
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    files = tuple(
        ExportedFile(table, path, _row_count(path), path.stat().st_size, _sha256(path))
        for table, path in written
    )
    record = {
        "schema_version": 1,
        "source": "data/snapshot/manifest.json",
        "snapshot_capture_date_utc": manifest.get("capture_date_utc"),
        "built_with": "agentic_bi.warehouse.build",
        "compression": COMPRESSION,
        "fact": {"table": schema.fact, "row_count": rows, "obligation_sum": f"{total:.2f}"},
        "total_bytes": sum(item.bytes for item in files),
        "files": [
            {
                "table": f.table,
                "path": f.path.name,
                "rows": f.rows,
                "bytes": f.bytes,
                "sha256": f.sha256,
            }
            for f in files
        ],
    }
    manifest_path = Path(out) / MANIFEST_NAME
    manifest_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    return ExportResult(files, rows, total, manifest_path)


def _row_count(path: Path) -> int:
    import duckdb

    connection = duckdb.connect()
    try:
        return connection.execute("SELECT count(*) FROM read_parquet(?)", [str(path)]).fetchone()[0]
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agentic_bi.powerbi.export",
        description="Build the warehouse and export the contract tables to Parquet.",
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="folder inside the repository")
    args = parser.parse_args(argv)
    try:
        out = inside(args.out, ROOT)
        result = export(out)
    except PowerBIError as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        return 1
    for item in result.files:
        print(f"{item.path.relative_to(ROOT)}  {item.rows} rows  {item.bytes} bytes")
    print(
        f"fact: {result.fact_rows} rows, obligations {result.fact_obligation_sum:.2f}, "
        "equal to data/snapshot/manifest.json"
    )
    total = result.total_bytes
    print(f"total: {total} bytes ({total / 1_000_000:.2f} MB)")
    if total >= COMMIT_LIMIT_BYTES:
        print("the files are 10 MB or more together: do not commit them (ADR-0004)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
