"""Generate the Power BI Desktop project (PBIP) of ADR-0004 from the contract and the catalog.

``uv run python -m agentic_bi.powerbi.generate --out powerbi`` writes:

- ``AgenticBI.pbip``, the project file Power BI Desktop opens;
- ``AgenticBI.SemanticModel/definition.pbism`` and the model in TMDL under
  ``AgenticBI.SemanticModel/definition/``: one table per contract table with the contract's
  columns and types, read from Parquet through the ``DataFolder`` parameter in import mode;
  the contract's many-to-one relationships; ``dim_date`` marked as the date table; one
  measure per catalog metric, with the catalog's description and the DAX of
  ``catalog/dax.yaml``;
- ``AgenticBI.Report/definition.pbir``, the report's pointer to the model.

These are the generator's files and are never edited by hand; a test regenerates them and
fails on any difference. The generator owns ``definition/`` entirely and removes any other
file it finds there. Report pages, visuals and every file whose schema Microsoft does not
publish (``report.json``, ``diagramLayout.json``) are left to Power BI Desktop. Files are
UTF-8 without a byte-order mark, with CRLF line endings.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path, PurePosixPath

from agentic_bi.semantic import Catalog, default_catalog

from . import ROOT, PowerBIError, inside, tmdl
from .dax import DaxMeasure, load_dax
from .schema import Column, StarSchema, Table, load_schema

PROJECT = "AgenticBI"
DEFAULT_OUT = ROOT / "powerbi"
SEMANTIC_MODEL = f"{PROJECT}.SemanticModel"
REPORT = f"{PROJECT}.Report"
DEFINITION = f"{SEMANTIC_MODEL}/definition"

PARAMETER = "DataFolder"
# A starting value only: each user sets the folder that holds data/model on their machine.
PARAMETER_DEFAULT = r"C:\agentic-bi\data\model"
COMPATIBILITY_LEVEL = 1567
CULTURE = "en-US"

# Contract type -> (TMDL dataType, Power Query type, column format string or None).
# DECIMAL(18,2) becomes a fixed decimal number (Currency.Type, four decimal places, held
# exactly), so sums stay exact to the cent; a DATE stays a date.
TYPE_MAP: dict[str, tuple[str, str, str | None]] = {
    "VARCHAR": ("string", "type text", None),
    "DATE": ("dateTime", "type date", "yyyy-mm-dd"),
    "DECIMAL(18,2)": ("decimal", "Currency.Type", "#,0.00"),
    "INTEGER": ("int64", "Int64.Type", "0"),
    "BOOLEAN": ("boolean", "type logical", None),
}
# Catalog unit -> measure format string. Shares show six places, as docs/golden/ does.
UNIT_FORMATS = {"usd": "#,0.00", "awards": "#,0", "share": "0.000000"}

_SCHEMA_BASE = "https://developer.microsoft.com/json-schemas/fabric"
_JSON_SCHEMAS = {
    "pbip": f"{_SCHEMA_BASE}/pbip/pbipProperties/1.0.0/schema.json",
    "pbism": f"{_SCHEMA_BASE}/item/semanticModel/definitionProperties/1.0.0/schema.json",
    "pbir": f"{_SCHEMA_BASE}/item/report/definitionProperties/2.0.0/schema.json",
}


def _json(value: dict) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _pbip() -> str:
    return _json(
        {
            "$schema": _JSON_SCHEMAS["pbip"],
            "version": "1.0",
            "artifacts": [{"report": {"path": REPORT}}],
            "settings": {"enableAutoRecovery": True},
        }
    )


def _pbism() -> str:
    # Version 4.0 and above declares a model held as TMDL in definition/.
    return _json({"$schema": _JSON_SCHEMAS["pbism"], "version": "4.0", "settings": {}})


def _pbir() -> str:
    return _json(
        {
            "$schema": _JSON_SCHEMAS["pbir"],
            "version": "4.0",
            "datasetReference": {"byPath": {"path": f"../{SEMANTIC_MODEL}"}},
        }
    )


def _database() -> str:
    return "\n".join(["database", f"{tmdl.INDENT}compatibilityLevel: {COMPATIBILITY_LEVEL}"])


def _model() -> str:
    lines = [
        "model Model",
        f"\tculture: {CULTURE}",
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        # Report authors use the catalog's measures, not implicit sums of columns.
        "\tdiscourageImplicitMeasures",
        f"\tsourceQueryCulture: {CULTURE}",
        "",
        # Auto date/time off: dim_date is the model's date table.
        "annotation __PBI_TimeIntelligenceEnabled = 0",
    ]
    return "\n".join(lines)


def _expressions() -> str:
    value = '"' + PARAMETER_DEFAULT.replace('"', '""') + '"'
    meta = 'meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]'
    lines = tmdl.description(
        "Folder holding the Parquet files of data/model, without a trailing backslash. "
        "Set it in Power BI Desktop: Transform data, Edit parameters.",
        0,
    )
    lines += tmdl.declaration(f"expression {tmdl.name(PARAMETER)}", f"{value} {meta}", 0)
    lines += [
        f"\tlineageTag: {tmdl.lineage_tag('expression', PARAMETER)}",
        "",
        "\tannotation PBI_ResultType = Text",
    ]
    return "\n".join(lines)


def _m_string(text: str) -> str:
    return '"' + text.replace('"', '""') + '"'


def _partition_source(table: Table) -> str:
    names = ", ".join(_m_string(column.name) for column in table.columns)
    types = ",\n".join(
        f"            {{{_m_string(column.name)}, {TYPE_MAP[column.type][1]}}}"
        for column in table.columns
    )
    file_name = _m_string("\\" + f"{table.name}.parquet")
    return "\n".join(
        [
            "let",
            f"    Source = Parquet.Document(File.Contents({PARAMETER} & {file_name})),",
            f"    Selected = Table.SelectColumns(Source, {{{names}}}),",
            "    Typed = Table.TransformColumnTypes(",
            "        Selected,",
            "        {",
            types,
            "        }",
            "    )",
            "in",
            "    Typed",
        ]
    )


def _plain(text: str) -> str:
    """Contract prose without Markdown code ticks, for a description."""
    return text.replace("`", "")


def _column(table: Table, column: Column, hidden: set[str], schema: StarSchema) -> list[str]:
    data_type, _, format_string = TYPE_MAP[column.type]
    lines = tmdl.description(_plain(column.meaning), 1) if column.meaning else []
    lines.append(f"\tcolumn {tmdl.name(column.name)}")
    lines.append(f"\t\tdataType: {data_type}")
    if column.name in hidden:
        lines.append("\t\tisHidden")
    if table.name == _DATE_TABLE and column.name == table.key:
        # With the table's dataCategory Time, this marks dim_date as the date table.
        lines.append("\t\tisKey")
    if format_string:
        lines.append(f"\t\tformatString: {format_string}")
    lines.append(f"\t\tlineageTag: {tmdl.lineage_tag('column', table.name, column.name)}")
    lines.append(f"\t\tsummarizeBy: {'sum' if data_type == 'decimal' else 'none'}")
    lines.append(f"\t\tsourceColumn: {column.name}")
    if column.type == "DATE":
        lines += ["", "\t\tannotation UnderlyingDateTimeDataType = Date"]
    return lines


_DATE_TABLE = "dim_date"


def _measure(table: Table, measure: DaxMeasure, catalog: Catalog) -> list[str]:
    metric = catalog.metrics[measure.name]
    lines = tmdl.description(metric.description, 1)
    lines += tmdl.declaration(f"measure {tmdl.name(measure.name)}", measure.expression, 1)
    lines.append(f"\t\tformatString: {UNIT_FORMATS[metric.unit]}")
    lines.append(f"\t\tlineageTag: {tmdl.lineage_tag('measure', table.name, measure.name)}")
    return lines


def _table(
    table: Table, schema: StarSchema, catalog: Catalog, measures: dict[str, DaxMeasure]
) -> str:
    lines = [f"table {tmdl.name(table.name)}"]
    if table.name == _DATE_TABLE:
        lines.append("\tdataCategory: Time")
    lines.append(f"\tlineageTag: {tmdl.lineage_tag('table', table.name)}")
    blocks: list[list[str]] = []
    if table.name == schema.fact:
        blocks += [_measure(table, measure, catalog) for measure in measures.values()]
    # The fact's join keys and its amount are hidden: report authors filter through the
    # dimensions and aggregate through the catalog's measures.
    hidden: set[str] = set()
    if table.name == schema.fact:
        hidden = {r.from_column for r in schema.relationships} | {"obligation_amount"}
    blocks += [_column(table, column, hidden, schema) for column in table.columns]
    partition = [f"\tpartition {tmdl.name(table.name)} = m", "\t\tmode: import"]
    partition += tmdl.declaration("source", _partition_source(table), 2)
    blocks.append(partition)
    blocks.append(["\tannotation PBI_ResultType = Table"])
    for block in blocks:
        lines.append("")
        lines += block
    return "\n".join(lines)


def _relationships(schema: StarSchema) -> str:
    lines: list[str] = []
    for item in schema.relationships:
        if lines:
            lines.append("")
        tag = tmdl.lineage_tag(
            "relationship", item.from_table, item.from_column, item.to_table, item.to_column
        )
        # Many-to-one and single-direction filtering are the defaults, so not written.
        lines += [
            f"relationship {tag}",
            f"\tfromColumn: {tmdl.column_ref(item.from_table, item.from_column)}",
            f"\ttoColumn: {tmdl.column_ref(item.to_table, item.to_column)}",
        ]
    return "\n".join(lines)


def project_files(
    schema: StarSchema | None = None,
    catalog: Catalog | None = None,
    measures: dict[str, DaxMeasure] | None = None,
) -> dict[str, bytes]:
    """Every generated file, keyed by its POSIX path relative to the output folder."""
    schema = schema or load_schema()
    catalog = catalog or default_catalog()
    measures = dict(measures if measures is not None else load_dax(catalog=catalog))
    texts = {
        f"{PROJECT}.pbip": _pbip(),
        f"{SEMANTIC_MODEL}/definition.pbism": _pbism(),
        f"{REPORT}/definition.pbir": _pbir(),
        f"{DEFINITION}/database.tmdl": _database(),
        f"{DEFINITION}/model.tmdl": _model(),
        f"{DEFINITION}/expressions.tmdl": _expressions(),
        f"{DEFINITION}/relationships.tmdl": _relationships(schema),
    }
    for table in schema.tables:
        texts[f"{DEFINITION}/tables/{table.name}.tmdl"] = _table(table, schema, catalog, measures)
    return {path: tmdl.to_crlf_bytes(text) for path, text in texts.items()}


def write_project(out: Path | str) -> list[str]:
    """Write the project into ``out``; remove files under definition/ it did not write.

    Returns the relative paths removed. No folder or file between ``out`` and a path it
    writes or removes may be a symbolic link, so a write or a removal never leaves ``out``.
    A symbolic link inside ``definition/`` is removed as a link; its target is not touched.
    """
    target = Path(out)
    files = project_files()
    definition = target / DEFINITION
    _refuse_links(target, definition)
    removed = []
    if definition.exists():
        for dirpath, dirnames, filenames in os.walk(definition, followlinks=False):
            # a linked folder is listed with the folders but never entered
            entries = filenames + [name for name in dirnames if (Path(dirpath) / name).is_symlink()]
            for filename in entries:
                path = Path(dirpath) / filename
                relative = PurePosixPath(path.relative_to(target).as_posix())
                if str(relative) not in files:
                    path.unlink()
                    removed.append(str(relative))
    for relative, content in files.items():
        path = target / relative
        _refuse_links(target, path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    if definition.exists():
        # bottom-up, so a chain of emptied folders goes in one pass
        for dirpath, _dirnames, _filenames in os.walk(definition, topdown=False):
            folder = Path(dirpath)
            if folder != definition and not any(folder.iterdir()):
                folder.rmdir()
    return sorted(removed)


def _refuse_links(target: Path, path: Path) -> None:
    """Raise when ``target`` or any part of ``path`` below it is a symbolic link."""
    relative = path.relative_to(target)
    current = target
    for part in (None, *relative.parts):
        if part is not None:
            current = current / part
        if current.is_symlink():
            raise PowerBIError(f"{current} is a symbolic link; refusing to write through it")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agentic_bi.powerbi.generate",
        description="Write the Power BI project (PBIP, TMDL) from the contract and the catalog.",
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="folder inside the repository")
    args = parser.parse_args(argv)
    try:
        out = inside(args.out, ROOT)
        removed = write_project(out)
    except PowerBIError as exc:
        print(f"generate failed: {exc}", file=sys.stderr)
        return 1
    for relative in sorted(project_files()):
        print((out / relative).relative_to(ROOT).as_posix())
    for relative in removed:
        print(f"removed {(out / relative).relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
