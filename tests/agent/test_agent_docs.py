"""docs/agent.md and the live questions file keep up with the code and the replays."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic_bi.agent.answer import REFUSAL_CODES
from agentic_bi.agent.cli import read_questions
from agentic_bi.agent.envelope import TOOL_NAMES
from agentic_bi.agent.settings import DEFAULT_MODEL

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs" / "agent.md"
ADR = ROOT / "docs" / "decisions" / "0005-analyst-agent-runtime.md"
HERE = Path(__file__).resolve().parent
LIVE_QUESTIONS = HERE / "live-questions.txt"


def test_the_guide_names_every_tool_refusal_code_and_command() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    for name in (*TOOL_NAMES, *REFUSAL_CODES, DEFAULT_MODEL, "ANTHROPIC_API_KEY"):
        assert f"`{name}`" in text, name
    assert "uv run python -m agentic_bi.agent ask" in text
    assert "uv run python -m agentic_bi.agent session --questions" in text
    assert "### What to check in the transcripts" in text


def test_the_adr_pins_the_library_and_names_the_default_model() -> None:
    text = ADR.read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"anthropic==2.3.0"' in pyproject
    assert "`anthropic==2.3.0`" in text and "https://pypi.org/project/anthropic/2.3.0/" in text
    assert f"`{DEFAULT_MODEL}`" in text


def test_the_live_questions_are_the_replay_questions_in_order() -> None:
    replays = sorted((HERE / "replays").glob("*.json"))
    expected = [json.loads(path.read_text("utf-8"))["question"] for path in replays]
    assert read_questions(LIVE_QUESTIONS) == expected


@pytest.mark.parametrize("path", [GUIDE, ADR, LIVE_QUESTIONS], ids=lambda path: path.name)
def test_no_em_or_en_dash(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert chr(0x2014) not in text and chr(0x2013) not in text


def test_the_guide_states_the_escapes_the_envelope_writes() -> None:
    from agentic_bi.agent.envelope import dumps

    text = GUIDE.read_text(encoding="utf-8")
    escaped = [dumps(char).strip('"') for char in "<>&"]
    assert escaped == ["\\u003c", "\\u003e", "\\u0026"]
    assert "written as `{}`, `{}` and `{}`".format(*escaped) in text
