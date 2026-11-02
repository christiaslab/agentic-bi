"""``python -m agentic_bi.reproduce``: build the warehouse, reproduce the golden values, report.

Builds the SQLMesh project with ``agentic_bi.warehouse.build`` into ``data/tmp/`` (a plan
with no change is quick), runs every mapped structured query through the read-only query
connection and writes golden, reproduced and difference per golden id as JSON. The exit
status is 0 when every outcome is the expected one (docs/reproduction.md), 1 otherwise, and
2 when the mapping or the golden file is malformed.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

DEFAULT_OUT = Path("data") / "tmp" / "reproduction.json"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agentic_bi.reproduce",
        description="Reproduce the golden values through the compiled structured queries.",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="the JSON report to write")
    parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="the DuckDB file to build and query (default data/tmp/warehouse.duckdb)",
    )
    parser.add_argument("--mapping", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--golden", type=Path, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    # Read by SQLMesh when it is first imported: load models in this process (no fork while
    # DuckDB threads run) and send no usage events (the project config turns them off too).
    os.environ.setdefault("MAX_FORK_WORKERS", "1")
    os.environ.setdefault("SQLMESH__DISABLE_ANONYMIZED_ANALYTICS", "1")

    from agentic_bi import warehouse
    from agentic_bi.semantic import open_query_connection

    from .mapping import DEFAULT_GOLDEN, DEFAULT_MAPPING, MappingError, load_golden, load_mapping
    from .run import report, reproduce, write_report

    golden_path = args.golden or DEFAULT_GOLDEN
    mapping_path = args.mapping or DEFAULT_MAPPING
    try:
        golden = load_golden(golden_path)
        mapping = load_mapping(mapping_path, golden)
    except (MappingError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    database = warehouse.build(args.database or warehouse.DEFAULT_DATABASE)
    connection = open_query_connection(database)
    try:
        outcomes = reproduce(connection, mapping, golden)
    finally:
        connection.close()
    document = report(
        outcomes,
        golden=golden,
        golden_path=golden_path,
        mapping_path=mapping_path,
        database=database,
    )
    write_report(document, args.out)
    _print(document)
    print(f"report: {args.out}")
    return 0 if not document["summary"]["unexpected"] else 1


def _print(document: dict) -> None:
    rows = [("id", "tag", "status", "golden", "reproduced", "difference")]
    for value in document["values"]:
        status = value["status"] + ("" if value["expected"] else " (unexpected)")
        rows.append(
            (
                value["id"],
                value["tag"],
                status,
                str(value["golden"]),
                "" if value["reproduced"] is None else str(value["reproduced"]),
                "" if value["difference"] is None else str(value["difference"]),
            )
        )
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    for row in rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)))
    counts = document["summary"]
    print(
        f"{counts['reproduced']} of {counts['values']} reproduced "
        f"({counts['reproduced_derived']} derived), {counts['differs']} differ "
        f"({counts['known_differences']} known), {counts['errors']} errors, "
        f"{counts['not_expressible']} not expressible; unexpected: "
        f"{', '.join(counts['unexpected']) or 'none'}"
    )


if __name__ == "__main__":
    sys.exit(main())
