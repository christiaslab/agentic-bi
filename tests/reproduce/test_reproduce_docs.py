"""docs/reproduction.md must keep up with evals/golden_queries.yaml."""

from __future__ import annotations

import pytest

from agentic_bi.reproduce import DEFAULT_MAPPING, MappedQuery, NotExpressible, load_mapping
from agentic_bi.reproduce.mapping import ROOT

GUIDE = ROOT / "docs" / "reproduction.md"
MAPPING = load_mapping()


def _table(text: str, header: str) -> dict[str, list[str]]:
    rows = text.split(header, 1)[1].split("\n\n", 1)[0].splitlines()[2:]
    table = {}
    for row in rows:
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        table[cells[0].strip("`")] = cells[1:]
    return table


def test_not_expressible_table_matches_the_mapping() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    table = _table(text, "| Golden id | Tag | Golden | Reason | Disposition |")
    entries = {e.id: e for e in MAPPING.entries if isinstance(e, NotExpressible)}
    assert set(table) == set(entries)
    for value_id, (_tag, _golden, reason, disposition) in table.items():
        assert reason == entries[value_id].reason, value_id
        assert disposition == entries[value_id].disposition.replace("_", " "), value_id


def test_every_known_difference_is_explained() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    known = [e for e in MAPPING.entries if isinstance(e, MappedQuery) and e.known_difference]
    assert known
    for entry in known:
        assert f"`{entry.id}`" in text


def test_guide_states_the_rule_and_the_rerun_command() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    assert "uv run python -m agentic_bi.reproduce --out data/tmp/reproduction.json" in text
    assert "| reproduced - golden | <= 0.5 * 10^-p" in text


@pytest.mark.parametrize("path", [GUIDE, DEFAULT_MAPPING])
def test_no_em_or_en_dash(path) -> None:
    text = path.read_text(encoding="utf-8")
    assert chr(0x2014) not in text and chr(0x2013) not in text
