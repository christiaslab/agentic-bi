"""Write a run's transcript: the full JSON record and a Markdown view for reading it.

The JSON holds everything the loop recorded (``loop``): settings, prompt version and hash,
every model turn as returned, every tool call with its input and the exact text sent back,
every rejected final reply, and the typed answer. The Markdown view shows the same in reading
order. Neither ever holds a credential: the loop never sees one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .envelope import json_safe

_SLUG = re.compile(r"[^a-z0-9]+")
_TICKS = re.compile(r"`+")
_MARKDOWN = re.compile(r"[\\`*_\[\]<>|!]")


def slug(text: str, limit: int = 48) -> str:
    """A file-name-safe stem made of the text's lowercase words."""
    words = _SLUG.sub("-", text.lower()).strip("-")
    return (words[:limit].rstrip("-") or "question") if words else "question"


def write_transcript(directory: Path, stem: str, transcript: dict[str, Any]) -> list[Path]:
    """Write ``<stem>.json`` and ``<stem>.md``; refuses to overwrite either."""
    directory.mkdir(parents=True, exist_ok=True)
    paths = [directory / f"{stem}.json", directory / f"{stem}.md"]
    for path in paths:
        if path.exists():
            raise FileExistsError(f"{path} exists; choose another --out directory")
    paths[0].write_text(
        json.dumps(json_safe(transcript), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    paths[1].write_text(render_markdown(transcript), encoding="utf-8")
    return paths


def render_markdown(transcript: dict[str, Any]) -> str:
    answer, prompt = transcript["answer"], transcript["prompt"]
    lines = [f"# {transcript['question']}", ""]
    lines += [
        f"- Status: **{answer['status']}**",
        f"- Model: `{answer['model']}`; {answer['model_calls']} model calls, "
        f"{answer['tool_calls']} tool calls",
        f"- Prompt: `{prompt['version']}` (sha256 `{prompt['sha256']}`)",
        f"- Provenance: {transcript['provenance']}",
        f"- Started {transcript['started_at']}, finished {transcript['finished_at']}",
        f"- Usage: {_usage(transcript.get('usage', {}))}",
        "",
        "## Answer",
        "",
    ]
    # text that came from the model is shown as plain text, never rendered as Markdown
    if answer["status"] == "answered":
        lines += _fenced(answer["text"], "text")
    elif answer["status"] == "refused":
        refusal = answer["refusal"]
        lines += [f"Refused with `{refusal['code']}`:", "", *_fenced(refusal["reason"], "text")]
        if refusal.get("compiler"):
            compiler = refusal["compiler"]
            message = f"{compiler['code']} at {compiler['field']}: {compiler['message']}"
            lines += ["", "Compiler refusal:", "", *_fenced(message, "text")]
    else:
        lines += ["Failed:", "", *_fenced(answer["failure"], "text")]
    lines += ["", "## Queries run", ""]
    if not answer["queries"]:
        lines.append("None.")
    for number, run in enumerate(answer["queries"], start=1):
        lines += [f"### Query {number}", "", *_fenced(json.dumps(run["query"]), "json"), ""]
        lines += [*_fenced(run["sql"], "sql"), "", "Parameters:", ""]
        lines += [*_fenced(json.dumps(run["params"]), "json"), ""]
        lines += [*_table(run["columns"], run["rows"]), ""]
    lines += ["## Conversation", ""]
    for event in transcript["events"]:
        lines += _event(event)
    return "\n".join(lines).rstrip() + "\n"


def _fenced(text: str, language: str) -> list[str]:
    """A code block whose fence is longer than any run of backticks in the text, so no text
    from the model or the data can close it and turn into Markdown."""
    longest = max((len(run) for run in _TICKS.findall(text)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}{language}", text, fence]


def _usage(usage: dict[str, Any]) -> str:
    if not usage:
        return "not reported"
    return ", ".join(f"{key} {value}" for key, value in usage.items())


def _table(columns: list[str], rows: list[list[Any]]) -> list[str]:
    def cell(value: Any) -> str:
        return "null" if value is None else _MARKDOWN.sub(r"\\\g<0>", str(value))

    lines = [f"Rows: {len(rows)}", ""]
    if columns:
        lines.append("| " + " | ".join(columns) + " |")
        lines.append("|" + " --- |" * len(columns))
        lines += ["| " + " | ".join(cell(value) for value in row) + " |" for row in rows]
    return lines


def _event(event: dict[str, Any]) -> list[str]:
    kind = event["event"]
    if kind == "question":
        return [f"**Question:** {event['text']}", ""]
    if kind == "model_turn":
        lines = [
            f"### Model turn {event['turn']} (`{event['model']}`, stop_reason "
            f"`{event['stop_reason']}`)",
            "",
        ]
        for block in event["content"]:
            kind_of = block.get("type")
            if kind_of == "thinking":
                summary = block.get("thinking") or "(not shown)"
                lines += ["Thinking (summary):", "", *_fenced(summary, "text"), ""]
            elif kind_of == "text":
                lines += ["Text:", "", *_fenced(block.get("text", ""), "text"), ""]
            elif kind_of == "tool_use":
                lines += [
                    f"Tool call `{block.get('name')}` (`{block.get('id')}`):",
                    "",
                    *_fenced(json.dumps(block.get("input"), indent=2), "json"),
                    "",
                ]
            else:
                lines += [f"Block of type `{kind_of}`.", ""]
        return lines
    if kind == "tool_call":
        status = "error" if event["is_error"] else "ok"
        return [
            f"Tool result `{event['name']}` ({status}), as the model received it:",
            "",
            *_fenced(event["result"], "text"),
            "",
        ]
    if kind == "final_reply":
        if event["accepted"]:
            return ["**Final reply accepted.**", ""]
        return ["**Final reply rejected:**", "", *_fenced(event["problem"], "text"), ""]
    if kind == "feedback":
        return ["Harness feedback sent to the model:", "", *_fenced(event["text"], "text"), ""]
    if kind == "stopped":
        return ["**Stopped:**", "", *_fenced(event["reason"], "text"), ""]
    return [f"Event `{kind}`.", ""]
