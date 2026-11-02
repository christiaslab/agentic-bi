"""A recipient name with a number in it can be named in an answer; the number is not a figure.

Ten labels of the 2026-11-01 warehouse hold a number (for example "CHENEGA SENTINEL 360,
LLC"). A name the schema tool returned, written out in full, is text; a number stated anywhere
else still has to appear in the rows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agent_fixture import build_agent_fixture

from agentic_bi.agent import ReplayModel, ToolBox, ask
from agentic_bi.agent.answer import ANSWERED, FAILED
from agentic_bi.semantic import open_query_connection

NAME = "SENTINEL 360, LLC"
FY = {"start": "2024-10-01", "end": "2025-09-30"}
TOP = {
    "metric": "obligations",
    "dimensions": ["recipient"],
    "time_range": FY,
    "order_by": {"field": "obligations", "direction": "desc"},
    "limit": 1,
}


def _turns(*answers: str, look_up: bool = True) -> list[dict[str, Any]]:
    def use(n: int, name: str, payload: Any) -> dict[str, Any]:
        return {"type": "tool_use", "id": f"toolu_name_{n}", "name": name, "input": payload}

    turns = [{"stop_reason": "tool_use", "content": [use(1, "run_query", TOP)]}]
    if look_up:
        lookup = use(2, "schema", {"dimension": "recipient", "codes": ["P01"]})
        turns.append({"stop_reason": "tool_use", "content": [lookup]})
    for text in answers:
        reply = json.dumps({"status": "answered", "answer": text})
        turns.append({"stop_reason": "end_turn", "content": [{"type": "text", "text": reply}]})
    return turns


@pytest.fixture
def toolbox(tmp_path: Path):
    path = build_agent_fixture(tmp_path / "names.duckdb", {"P01": NAME})
    connection = open_query_connection(path)
    try:
        yield ToolBox(connection)
    finally:
        connection.close()


def test_a_name_with_a_number_can_be_written_out(toolbox) -> None:
    text = f"The largest recipient was {NAME} (P01), with $2,300.00 of net obligations."
    result = ask("Largest recipient?", model=ReplayModel(_turns(text)), toolbox=toolbox)
    assert result.answer.status == ANSWERED and result.answer.text == text


def test_the_name_matches_in_any_case(toolbox) -> None:
    text = "The largest recipient was Sentinel 360, LLC, with $2,300.00 of net obligations."
    result = ask("Largest recipient?", model=ReplayModel(_turns(text)), toolbox=toolbox)
    assert result.answer.status == ANSWERED


def test_the_number_alone_is_still_a_figure(toolbox) -> None:
    text = f"{NAME} had 360 awards and $2,300.00 of net obligations."
    model = ReplayModel(_turns(text, text))
    result = ask("Largest recipient?", model=model, toolbox=toolbox)
    assert result.answer.status == FAILED and "do not appear in the rows" in result.answer.failure
    assert "360" in result.answer.failure


def test_a_name_never_looked_up_is_not_text(toolbox) -> None:
    text = f"The largest recipient was {NAME}, with $2,300.00 of net obligations."
    model = ReplayModel(_turns(text, text, look_up=False))
    result = ask("Largest recipient?", model=model, toolbox=toolbox)
    assert result.answer.status == FAILED and "360" in result.answer.failure
