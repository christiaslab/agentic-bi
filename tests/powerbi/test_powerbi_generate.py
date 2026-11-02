"""The generated PBIP project: in sync with the generator, file hygiene, and model content."""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from agentic_bi.powerbi import ROOT, PowerBIError, generate, tmdl
from agentic_bi.powerbi.dax import load_dax
from agentic_bi.powerbi.schema import load_schema
from agentic_bi.semantic import default_catalog

COMMITTED = ROOT / "powerbi"
DEFINITION = COMMITTED / generate.DEFINITION
SCHEMA = load_schema()
CATALOG = default_catalog()
FILES = generate.project_files()
# The Windows path limit is 260 characters; a clone path of up to 160 characters still fits.
MAX_RELATIVE_PATH = 100


def _text(relative: str) -> str:
    return FILES[relative].decode("utf-8").replace("\r\n", "\n")


def _table_text(name: str) -> str:
    return _text(f"{generate.DEFINITION}/tables/{name}.tmdl")


def _objects(text: str, keyword: str, depth: int) -> dict[str, list[str]]:
    """Each ``keyword name`` object at ``depth`` with its following deeper lines."""
    indent = "\t" * depth
    objects: dict[str, list[str]] = {}
    current = None
    for line in text.split("\n"):
        if line.startswith(f"{indent}{keyword} "):
            head = line[len(indent) + len(keyword) + 1 :]
            current = head.split(" = ")[0].rstrip(" =")
            objects[current] = [line]
        elif current is not None and (line.startswith(indent + "\t") or not line.strip()):
            objects[current].append(line)
        else:
            current = None
    return objects


def _description(text: str, keyword: str, name: str, depth: int) -> str:
    lines = text.split("\n")
    indent = "\t" * depth
    index = next(
        i
        for i, line in enumerate(lines)
        if line == f"{indent}{keyword} {name}" or line.startswith(f"{indent}{keyword} {name} =")
    )
    found = []
    while index > 0 and lines[index - 1].startswith(f"{indent}/// "):
        index -= 1
        found.insert(0, lines[index][len(indent) + 4 :])
    return "\n".join(found)


# In sync: the committed project is exactly what the generator writes.


def test_regenerating_into_a_temporary_folder_matches_the_committed_project(tmp_path) -> None:
    removed = generate.write_project(tmp_path)
    assert removed == []
    written = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert written == sorted(FILES)
    differing = [
        relative
        for relative in FILES
        if not (COMMITTED / relative).is_file()
        or (COMMITTED / relative).read_bytes() != (tmp_path / relative).read_bytes()
    ]
    assert differing == [], "run: uv run python -m agentic_bi.powerbi.generate --out powerbi"


def test_the_committed_definition_folder_holds_only_generated_files() -> None:
    committed = {p.relative_to(COMMITTED).as_posix() for p in DEFINITION.rglob("*") if p.is_file()}
    generated = {relative for relative in FILES if relative.startswith(f"{generate.DEFINITION}/")}
    assert committed == generated


def test_generation_is_stable() -> None:
    assert generate.project_files() == FILES


# File hygiene (ADR-0004 point 6).


@pytest.mark.parametrize("relative", sorted(FILES))
def test_files_are_utf8_without_bom_with_crlf_line_endings(relative) -> None:
    for content in (FILES[relative], (COMMITTED / relative).read_bytes()):
        assert not content.startswith(b"\xef\xbb\xbf")
        content.decode("utf-8")
        assert content.endswith(b"\r\n")
        assert content.count(b"\n") == content.count(b"\r\n") == content.count(b"\r")
        assert not re.search(rb"[ \t]\r\n", content), "trailing whitespace"


@pytest.mark.parametrize("relative", sorted(FILES))
def test_paths_stay_short(relative) -> None:
    path = f"powerbi/{relative}"
    assert len(path) <= MAX_RELATIVE_PATH
    assert len(path) < 80, "docs/powerbi.md says the longest path is under 80 characters"
    assert re.fullmatch(r"[A-Za-z0-9_./]+", path)


def test_gitattributes_keeps_crlf_for_every_generated_file() -> None:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    paths = [f"powerbi/{relative}" for relative in sorted(FILES)]
    result = subprocess.run(
        ["git", "check-attr", "text", "eol", "--", *paths],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    attributes = {}
    for line in result.stdout.splitlines():
        path, name, value = line.rsplit(": ", 2)
        attributes[(path, name)] = value
    for path in paths:
        assert attributes[(path, "text")] == "set", path
        assert attributes[(path, "eol")] == "crlf", path


def test_gitignore_leaves_out_the_desktop_cache_and_local_settings() -> None:
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    paths = [
        f"powerbi/{generate.SEMANTIC_MODEL}/.pbi/cache.abf",
        f"powerbi/{generate.SEMANTIC_MODEL}/.pbi/localSettings.json",
        f"powerbi/{generate.REPORT}/.pbi/localSettings.json",
    ]
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--", *paths],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert sorted(result.stdout.split()) == sorted(paths)


# The JSON pointers.


def test_the_json_files_point_at_each_other() -> None:
    pbip = json.loads(_text(f"{generate.PROJECT}.pbip"))
    assert pbip["artifacts"] == [{"report": {"path": generate.REPORT}}]
    pbir = json.loads(_text(f"{generate.REPORT}/definition.pbir"))
    assert pbir["datasetReference"] == {"byPath": {"path": f"../{generate.SEMANTIC_MODEL}"}}
    pbism = json.loads(_text(f"{generate.SEMANTIC_MODEL}/definition.pbism"))
    assert pbism["version"] == "4.0"
    for document in (pbip, pbir, pbism):
        assert document["$schema"].startswith("https://developer.microsoft.com/json-schemas/")


def test_no_file_without_a_published_schema_is_written() -> None:
    names = {relative.rsplit("/", 1)[-1] for relative in FILES}
    assert not names & {"report.json", "diagramLayout.json", "cache.abf", "localSettings.json"}
    report_files = [relative for relative in FILES if relative.startswith(generate.REPORT)]
    assert report_files == [f"{generate.REPORT}/definition.pbir"]


# Model content: tables, columns, relationships, date table, parameter, measures.


def test_one_table_file_per_contract_table() -> None:
    tables = {
        relative.rsplit("/", 1)[-1].removesuffix(".tmdl")
        for relative in FILES
        if relative.startswith(f"{generate.DEFINITION}/tables/")
    }
    assert tables == {table.name for table in SCHEMA.tables}
    for table in SCHEMA.tables:
        assert _table_text(table.name).startswith(f"table {table.name}\n")


@pytest.mark.parametrize("table", SCHEMA.tables, ids=lambda table: table.name)
def test_each_table_has_the_contract_columns_and_types(table) -> None:
    columns = _objects(_table_text(table.name), "column", 1)
    assert list(columns) == [column.name for column in table.columns]
    for column in table.columns:
        body = columns[column.name]
        assert f"\t\tdataType: {generate.TYPE_MAP[column.type][0]}" in body
        assert f"\t\tsourceColumn: {column.name}" in body


@pytest.mark.parametrize("table", SCHEMA.tables, ids=lambda table: table.name)
def test_each_partition_reads_its_parquet_file_through_the_parameter(table) -> None:
    partitions = _objects(_table_text(table.name), "partition", 1)
    assert list(partitions) == [table.name]
    body = "\n".join(partitions[table.name])
    assert f"partition {table.name} = m" in body
    assert "\t\tmode: import" in body
    assert f'File.Contents({generate.PARAMETER} & "\\{table.name}.parquet")' in body
    for column in table.columns:
        assert f'{{"{column.name}", {generate.TYPE_MAP[column.type][1]}}}' in body


def test_the_data_folder_parameter_is_a_required_text_parameter() -> None:
    text = _text(f"{generate.DEFINITION}/expressions.tmdl")
    expressions = _objects(text, "expression", 0)
    assert list(expressions) == [generate.PARAMETER]
    head = expressions[generate.PARAMETER][0]
    assert "IsParameterQuery=true" in head
    assert 'Type="Text"' in head
    assert "IsParameterQueryRequired=true" in head


def test_the_relationships_are_the_contract_joins() -> None:
    text = _text(f"{generate.DEFINITION}/relationships.tmdl")
    found = []
    for body in _objects(text, "relationship", 0).values():
        properties = dict(line.strip().split(": ", 1) for line in body[1:] if ": " in line)
        assert set(properties) == {"fromColumn", "toColumn"}
        found.append((properties["fromColumn"], properties["toColumn"]))
    expected = [
        (f"{r.from_table}.{r.from_column}", f"{r.to_table}.{r.to_column}")
        for r in SCHEMA.relationships
    ]
    assert found == expected
    assert len(expected) == 7
    assert all(item[0].startswith(f"{SCHEMA.fact}.") for item in found)


def test_dim_date_is_the_date_table() -> None:
    marked = [
        table.name for table in SCHEMA.tables if "\tdataCategory: Time" in _table_text(table.name)
    ]
    assert marked == ["dim_date"]
    columns = _objects(_table_text("dim_date"), "column", 1)
    keys = [name for name, body in columns.items() if "\t\tisKey" in body]
    assert keys == ["date"]
    assert "\t\tdataType: dateTime" in columns["date"]


def test_auto_date_time_is_off_and_implicit_measures_are_discouraged() -> None:
    text = _text(f"{generate.DEFINITION}/model.tmdl")
    assert "annotation __PBI_TimeIntelligenceEnabled = 0" in text.split("\n")
    assert "\tdiscourageImplicitMeasures" in text.split("\n")


def test_one_measure_per_catalog_metric_with_its_description_and_dax() -> None:
    text = _table_text(SCHEMA.fact)
    measures = _objects(text, "measure", 1)
    assert list(measures) == list(CATALOG.metrics)
    dax = load_dax()
    for name, metric in CATALOG.metrics.items():
        assert _description(text, "measure", name, 1) == metric.description
        body = measures[name]
        head = body[0]
        if head.endswith(" ="):
            lines = [line[3:] for line in body[1:] if line.startswith("\t\t\t")]
            expression = "\n".join(lines)
        else:
            expression = head.split(" = ", 1)[1]
        assert expression == dax[name].expression
        assert f"\t\tformatString: {generate.UNIT_FORMATS[metric.unit]}" in body
    for table in SCHEMA.tables:
        if table.name != SCHEMA.fact:
            assert "\tmeasure " not in _table_text(table.name)


def test_the_fact_hides_its_join_keys_and_its_amount_only() -> None:
    columns = _objects(_table_text(SCHEMA.fact), "column", 1)
    hidden = {name for name, body in columns.items() if "\t\tisHidden" in body}
    keys = {r.from_column for r in SCHEMA.relationships}
    assert hidden == keys | {"obligation_amount"}
    for table in SCHEMA.tables:
        if table.name != SCHEMA.fact:
            assert "isHidden" not in _table_text(table.name)


def test_lineage_tags_are_unique() -> None:
    tags = [
        line.split(": ", 1)[1]
        for content in FILES.values()
        for line in content.decode("utf-8").splitlines()
        if line.strip().startswith("lineageTag: ")
    ]
    relationships = _objects(_text(f"{generate.DEFINITION}/relationships.tmdl"), "relationship", 0)
    tags += list(relationships)
    assert len(tags) == len(set(tags))
    expected = 1 + len(SCHEMA.tables) + sum(len(t.columns) for t in SCHEMA.tables)
    assert len(tags) == expected + len(CATALOG.metrics) + len(SCHEMA.relationships)


# Writers.


@pytest.mark.parametrize(
    ("value", "written"),
    [
        ("fiscal_month", "fiscal_month"),
        ("_private", "_private"),
        ("Fiscal Month", "'Fiscal Month'"),
        ("O'Brien", "'O''Brien'"),
        ("2025", "'2025'"),
        ("a.b", "'a.b'"),
        ("a=b", "'a=b'"),
        ("a:b", "'a:b'"),
    ],
)
def test_names_with_spaces_or_symbols_are_quoted(value, written) -> None:
    assert tmdl.name(value) == written


@pytest.mark.parametrize("value", ["", "a\nb", "a\tb", "a\rb"])
def test_names_that_cannot_be_written_are_refused(value) -> None:
    with pytest.raises(PowerBIError):
        tmdl.name(value)


def test_references_quote_each_part() -> None:
    assert tmdl.column_ref("dim_date", "date") == "dim_date.date"
    assert tmdl.column_ref("Dim Date", "Fiscal Month") == "'Dim Date'.'Fiscal Month'"
    assert tmdl.dax_column("dim_date", "date") == "dim_date[date]"
    assert tmdl.dax_column("Dim Date", "a]b") == "'Dim Date'[a]]b]"


def test_a_multi_line_expression_is_indented_two_levels_deeper() -> None:
    lines = tmdl.declaration("measure m", "VAR x = 1\nRETURN\n    x", 1)
    assert lines == ["\tmeasure m =", "\t\t\tVAR x = 1", "\t\t\tRETURN", "\t\t\t    x"]
    assert tmdl.declaration("measure m", "  SUM ( t[c] )  ", 1) == ["\tmeasure m = SUM ( t[c] )"]


@pytest.mark.parametrize("expression", ["a\n\nb", "a\n\tb", "a\r\nb"])
def test_an_expression_with_a_blank_line_tab_or_carriage_return_is_refused(expression) -> None:
    with pytest.raises(PowerBIError):
        tmdl.declaration("measure m", expression, 1)


def test_descriptions_are_triple_slash_lines() -> None:
    assert tmdl.description("one\ntwo", 1) == ["\t/// one", "\t/// two"]
    with pytest.raises(PowerBIError):
        tmdl.description("  ", 0)


def test_crlf_conversion_refuses_carriage_returns() -> None:
    assert tmdl.to_crlf_bytes("a\nb") == b"a\r\nb\r\n"
    with pytest.raises(PowerBIError):
        tmdl.to_crlf_bytes("a\r\nb")


# Writing: stale files, the owner's files, symbolic links, and the command's folder check.


def test_writing_removes_stale_files_from_definition_only(tmp_path) -> None:
    generate.write_project(tmp_path)
    stale = tmp_path / generate.DEFINITION / "tables" / "old_table.tmdl"
    stale.write_text("table old_table\n", encoding="utf-8")
    culture = tmp_path / generate.DEFINITION / "cultures" / "en-US.tmdl"
    culture.parent.mkdir()
    culture.write_text("cultureInfo en-US\n", encoding="utf-8")
    owner = tmp_path / generate.REPORT / "report.json"
    owner.write_text("{}", encoding="utf-8")
    layout = tmp_path / generate.SEMANTIC_MODEL / "diagramLayout.json"
    layout.write_text("{}", encoding="utf-8")
    edited = tmp_path / generate.DEFINITION / "model.tmdl"
    edited.write_bytes(b"model Model\r\n")

    removed = generate.write_project(tmp_path)

    assert removed == [
        f"{generate.DEFINITION}/cultures/en-US.tmdl",
        f"{generate.DEFINITION}/tables/old_table.tmdl",
    ]
    assert not stale.exists() and not culture.parent.exists()
    assert owner.read_text(encoding="utf-8") == "{}"
    assert layout.exists()
    assert edited.read_bytes() == FILES[f"{generate.DEFINITION}/model.tmdl"]


def test_writing_refuses_a_definition_folder_that_is_a_symbolic_link(tmp_path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    keep = elsewhere / "keep.tmdl"
    keep.write_text("keep", encoding="utf-8")
    out = tmp_path / "out"
    (out / generate.SEMANTIC_MODEL).mkdir(parents=True)
    (out / generate.DEFINITION).symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(PowerBIError, match="symbolic link"):
        generate.write_project(out)
    assert keep.exists()


def test_writing_does_not_follow_a_symbolic_link_inside_definition(tmp_path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    keep = elsewhere / "keep.tmdl"
    keep.write_text("keep", encoding="utf-8")
    out = tmp_path / "out"
    generate.write_project(out)
    link = out / generate.DEFINITION / "linked"
    link.symlink_to(elsewhere, target_is_directory=True)
    generate.write_project(out)
    assert keep.exists()


def test_the_command_refuses_a_folder_outside_the_repository(tmp_path, capsys) -> None:
    assert generate.main(["--out", str(tmp_path / "project")]) == 1
    assert "outside" in capsys.readouterr().err
    assert not (tmp_path / "project").exists()


@pytest.mark.parametrize("linked", [generate.SEMANTIC_MODEL, generate.REPORT])
def test_writing_refuses_a_linked_project_folder(tmp_path, linked) -> None:
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "definition").mkdir(parents=True)
    keep = elsewhere / "definition" / "keep.tmdl"
    keep.write_text("keep", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    (out / linked).symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(PowerBIError, match="symbolic link"):
        generate.write_project(out)
    assert keep.read_text(encoding="utf-8") == "keep"
    assert sorted(p.name for p in elsewhere.rglob("*")) == ["definition", "keep.tmdl"]


def test_writing_refuses_a_generated_file_that_is_a_link(tmp_path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    (out / f"{generate.PROJECT}.pbip").symlink_to(outside)
    with pytest.raises(PowerBIError, match="symbolic link"):
        generate.write_project(out)
    assert outside.read_text(encoding="utf-8") == "keep"


def test_writing_removes_a_chain_of_stale_folders(tmp_path) -> None:
    generate.write_project(tmp_path)
    nested = tmp_path / generate.DEFINITION / "a" / "b"
    nested.mkdir(parents=True)
    (nested / "old.tmdl").write_text("x", encoding="utf-8")
    generate.write_project(tmp_path)
    assert not (tmp_path / generate.DEFINITION / "a").exists()
    assert (tmp_path / generate.DEFINITION / "tables").is_dir()
