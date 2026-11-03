"""Offline replay: hand-written model turns drive the real tool layer on the built warehouse.

Each file under ``replays/`` holds a question, model turns written by hand from the tool
definitions (never model output, and marked as such), and what must come out: the status,
how many queries ran, and for each golden value the row that must reproduce it. The warehouse
is built from the committed snapshot; the answers' figures are checked twice: by the agent's
own rule (every figure in the rows) and here against ``docs/golden/golden.json``.
"""

from __future__ import annotations

import json
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

import pytest

from agentic_bi import warehouse
from agentic_bi.agent import ReplayModel, ToolBox, ask
from agentic_bi.agent.answer import ANSWERED, REFUSED, QueryRun
from agentic_bi.agent.envelope import CLOSE, OPEN
from agentic_bi.agent.final import parse_final_reply
from agentic_bi.agent.model import REPLAY_PROVENANCE
from agentic_bi.semantic.catalog import default_catalog

REPLAYS_DIR = Path(__file__).resolve().parent / "replays"
GOLDEN_FILE = warehouse.ROOT / "docs" / "golden" / "golden.json"
GOLDEN = {item["id"]: item for item in json.loads(GOLDEN_FILE.read_text("utf-8"))["values"]}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


REPLAYS = {path.stem: _load(path) for path in sorted(REPLAYS_DIR.glob("*.json"))}
ALL = sorted(REPLAYS)
ANSWERED_IDS = [name for name in ALL if REPLAYS[name]["expect"]["status"] == ANSWERED]
REFUSED_IDS = [name for name in ALL if REPLAYS[name]["expect"]["status"] == REFUSED]
RATIO_IDS = [name for name in ALL if "golden_ratio" in REPLAYS[name]["expect"]]
DIFFERENCE_IDS = [name for name in ALL if "known_differences" in REPLAYS[name]["expect"]]


def _row(run: QueryRun, where: dict[str, Any]) -> tuple[Any, ...]:
    found = [
        row
        for row in run.rows
        if all(row[run.columns.index(column)] == value for column, value in where.items())
    ]
    assert len(found) == 1, f"{len(found)} rows match {where} in {run.columns}"
    return found[0]


def _equals_golden(cell: Any, golden: Any) -> bool:
    """Amounts and counts exactly; a DOUBLE ratio at the golden's six places, half even."""
    if isinstance(golden, int):
        return type(cell) is int and cell == golden
    expected = Decimal(golden)
    if isinstance(cell, Decimal):
        return cell == expected and cell.as_tuple().exponent == expected.as_tuple().exponent
    assert isinstance(cell, float), f"unexpected cell type {type(cell).__name__}"
    rounded = Decimal(repr(cell)).quantize(expected, rounding=ROUND_HALF_EVEN)
    return rounded == expected


@pytest.fixture(scope="module")
def results(warehouse_conn) -> dict[str, dict[str, Any]]:
    """Every replay, run once through the loop and the tools on the built warehouse."""
    runs = {}
    for name, replay in REPLAYS.items():
        model = ReplayModel(replay["turns"])
        result = ask(replay["question"], model=model, toolbox=ToolBox(warehouse_conn))
        runs[name] = {"replay": replay, "model": model, "result": result}
    return runs


@pytest.mark.parametrize("name", ALL)
def test_replay_is_marked_as_written_by_hand(results, name: str) -> None:
    replayed = results[name]
    replay = replayed["replay"]
    assert replay["provenance"] == "hand-written"
    assert "No model produced these turns and they are not model output" in replay["note"]
    transcript = replayed["result"].transcript
    assert transcript["provenance"] == REPLAY_PROVENANCE
    assert {event["model"] for event in transcript["events"] if "model" in event} == {
        replayed["model"].model
    }
    assert "not model output" in replayed["result"].answer.model


@pytest.mark.parametrize("name", ALL)
def test_replay_runs_to_its_last_turn_with_the_expected_outcome(results, name: str) -> None:
    replayed = results[name]
    expect, answer = replayed["replay"]["expect"], replayed["result"].answer
    assert replayed["model"].exhausted
    assert answer.status == expect["status"], answer.failure
    assert len(answer.queries) == expect["queries"]
    assert answer.model_calls == len(replayed["replay"]["turns"])


@pytest.mark.parametrize("name", ANSWERED_IDS)
def test_replay_figures_equal_the_golden_values(results, name: str) -> None:
    replayed = results[name]
    expect, answer = replayed["replay"]["expect"], replayed["result"].answer
    for item in expect.get("golden", []):
        golden = GOLDEN[item["id"]]
        run = answer.queries[item["run"]]
        cell = _row(run, item["where"])[run.columns.index(run.metric)]
        assert _equals_golden(cell, golden["value"]), (item["id"], cell, golden["value"])
        if "text" in item:
            assert item["text"] in answer.text, item["text"]


@pytest.mark.parametrize("name", RATIO_IDS)
def test_replay_ratio_equals_the_ratio_of_golden_values(results, name: str) -> None:
    replayed = results[name]
    ratio = replayed["replay"]["expect"]["golden_ratio"]
    run = replayed["result"].answer.queries[ratio["run"]]
    cell = _row(run, ratio["where"])[run.columns.index(run.metric)]
    numerator = sum(Decimal(GOLDEN[name]["value"]) for name in ratio["numerator"])
    denominator = sum(Decimal(GOLDEN[name]["value"]) for name in ratio["denominator"])
    quantum = Decimal(1).scaleb(-ratio["places"])
    expected = (numerator / denominator).quantize(quantum, rounding=ROUND_HALF_EVEN)
    assert Decimal(repr(cell)).quantize(quantum, rounding=ROUND_HALF_EVEN) == expected


@pytest.mark.parametrize("name", DIFFERENCE_IDS)
def test_replay_known_differences_still_hold(results, name: str) -> None:
    replayed = results[name]
    for item in replayed["replay"]["expect"].get("known_differences", []):
        golden = GOLDEN[item["id"]]
        run = replayed["result"].answer.queries[item["absent_from_run"]]
        codes = {row[run.columns.index(item["column"])] for row in run.rows}
        assert golden["dimensions"]["recipient_uei"] not in codes, item["reason"]


@pytest.mark.parametrize("name", REFUSED_IDS)
def test_replay_refusal_names_the_catalog_reason(results, name: str) -> None:
    replayed = results[name]
    expect, answer = replayed["replay"]["expect"], replayed["result"].answer
    assert answer.refusal.code == expect["code"]
    if expect["compiler"]:
        assert answer.refusal.compiler is not None
        assert answer.refusal.compiler["code"] == expect["code"]
    else:
        assert answer.refusal.compiler is None


@pytest.mark.parametrize("name", ALL)
def test_replay_tool_errors_are_the_expected_ones(results, name: str) -> None:
    replayed = results[name]
    events = replayed["result"].transcript["events"]
    errors = [
        json.loads(event["result"].split("\n")[1])["error"]["code"]
        for event in events
        if event["event"] == "tool_call" and event["is_error"]
    ]
    expected = replayed["replay"]["expect"].get("errors", [])
    if replayed["replay"]["expect"].get("compiler"):
        expected = [*expected, replayed["replay"]["expect"]["code"]]
    assert errors == expected


@pytest.mark.parametrize("name", ALL)
def test_replay_history_is_append_only_and_results_are_wrapped(results, name: str) -> None:
    replayed = results[name]
    requests = replayed["model"].requests
    for before, after in zip(requests, requests[1:], strict=False):
        assert after["messages"][: len(before["messages"])] == before["messages"]
        assert (after["system"], after["tools"]) == (before["system"], before["tools"])
    for message in requests[-1]["messages"]:
        if message["role"] == "user" and isinstance(message["content"], list):
            for block in message["content"]:
                assert block["type"] == "tool_result"
                assert block["content"].startswith(OPEN) and block["content"].endswith(CLOSE)
                assert block["content"].count(OPEN) == block["content"].count(CLOSE) == 1


@pytest.mark.parametrize("name", ALL)
def test_replay_final_turn_is_the_reply_the_agent_kept(results, name: str) -> None:
    replayed = results[name]
    final_text = replayed["replay"]["turns"][-1]["content"][0]["text"]
    reply = parse_final_reply(final_text)
    answer = replayed["result"].answer
    assert reply.status == answer.status
    if answer.status == ANSWERED:
        assert reply.answer == answer.text
    else:
        assert (reply.code, reply.reason) == (answer.refusal.code, answer.refusal.reason)


def test_the_replays_cover_what_the_brief_asks_for() -> None:
    replays = list(REPLAYS.values())
    answered = [r for r in replays if r["expect"]["status"] == ANSWERED]
    refused = [r for r in replays if r["expect"]["status"] == REFUSED]
    assert len(answered) >= 10 and len(refused) >= 2
    covers = {item for replay in replays for item in replay["covers"]}
    for metric in default_catalog().metrics:
        assert f"metric {metric}" in covers, metric
    for need in ("grain fiscal_month", "dimension recipient", "filter pop_state eq", "two steps"):
        assert need in covers, need
    top5 = [r for r in answered if "dimension recipient" in r["covers"]]
    assert any(turn_input(r).get("limit") == 5 for r in top5)
    assert len({replay["id"] for replay in replays}) == len(replays)


def turn_input(replay: dict[str, Any]) -> dict[str, Any]:
    """The input of the replay's first run_query with an order_by."""
    for turn in replay["turns"]:
        for block in turn["content"]:
            if block.get("name") == "run_query" and "order_by" in block.get("input", {}):
                return block["input"]
    return {}
