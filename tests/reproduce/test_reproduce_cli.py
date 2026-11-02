"""``python -m agentic_bi.reproduce``: build, reproduce, write the report, set the exit status.

The CLI builds into a database of its own here, not the session's: SQLMesh opens its file
read-write, which DuckDB refuses while a read-only connection to the same file is open.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agentic_bi import warehouse
from agentic_bi.reproduce import DEFAULT_MAPPING
from agentic_bi.reproduce.__main__ import main


@pytest.fixture(scope="module")
def cli_database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("cli") / warehouse.DEFAULT_DATABASE.name


def test_cli_writes_golden_reproduced_and_difference_per_id(
    cli_database: Path, tmp_path: Path, capsys
) -> None:
    out = tmp_path / "nested" / "reproduction.json"
    assert main(["--out", str(out), "--database", str(cli_database)]) == 0
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["golden_capture_date"] == "2026-11-01"
    assert len(document["values"]) == 48
    assert len({value["id"] for value in document["values"]}) == 48
    for value in document["values"]:
        assert {"golden", "reproduced", "difference"} <= set(value)
    counts = document["summary"]
    assert counts["unexpected"] == []
    assert counts["reproduced"] + counts["differs"] + counts["errors"] == counts["mapped"]
    assert counts["mapped"] + counts["not_expressible"] == 48
    printed = capsys.readouterr().out
    assert "obligations.fiscal_month.04" in printed
    assert "unexpected: none" in printed


def test_cli_exits_1_when_an_outcome_is_unexpected(
    cli_database: Path, tmp_path: Path, capsys
) -> None:
    # without its known_difference, the Weston recipient becomes an unexpected difference
    data = yaml.safe_load(DEFAULT_MAPPING.read_text(encoding="utf-8"))
    for entry in data["values"]:
        entry.pop("known_difference", None)
    mapping = tmp_path / "mapping.yaml"
    mapping.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    out = tmp_path / "reproduction.json"
    code = main(["--out", str(out), "--database", str(cli_database), "--mapping", str(mapping)])
    assert code == 1
    unexpected = json.loads(out.read_text(encoding="utf-8"))["summary"]["unexpected"]
    assert unexpected == ["obligations.recipient.Z4XGALW7L9K6"]
    assert "(unexpected)" in capsys.readouterr().out


def test_cli_exits_2_on_a_malformed_mapping_before_building(tmp_path: Path, capsys) -> None:
    mapping = tmp_path / "mapping.yaml"
    mapping.write_text("version: 1\nvalues: []\n", encoding="utf-8")
    database = tmp_path / warehouse.DEFAULT_DATABASE.name
    out = tmp_path / "reproduction.json"
    code = main(["--out", str(out), "--database", str(database), "--mapping", str(mapping)])
    assert code == 2
    assert "golden_capture_date" in capsys.readouterr().err
    assert not database.exists() and not out.exists()
