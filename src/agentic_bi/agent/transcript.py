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
    if answer["status"] == "answered":
        lines.append(answer["text"])
    elif answer["status"] == "refused":
        refusal = answer["refusal"]
        lines.append(f"Refused, `{refusal['code']}`: {refusal['reason']}")
        if refusal.get("compiler"):
            compiler = refusal["compiler"]
            lines += [
                "",
                f"Compiler refusal: `{compiler['code']}` at `{compiler['field']}`: "
                f"{compiler['message']}",
            ]
    else:
        lines.append(f"Failed: {answer['failure']}")
    lines += ["", "## Queries run", ""]
    if not answer["queries"]:
        lines.append("None.")
    for number, run in enumerate(answer["queries"], start=1):
        lines += [f"### Query {number}", "", "```json", json.dumps(run["query"]), "```", ""]
        lines += ["```sql", run["sql"], "```", "", f"Parameters: `{json.dumps(run['params'])}`"]
        lines += ["", *_table(run["columns"], run["rows"]), ""]
    lines += ["## Conversation", ""]
    for event in transcript["events"]:
        lines += _event(event)
    return "\n".join(lines).rstrip() + "\n"


def _usage(usage: dict[str, Any]) -> str:
    if not usage:
        return "not reported"
    return ", ".join(f"{key} {value}" for key, value in usage.items())


def _table(columns: list[str], rows: list[list[Any]]) -> list[str]:
    def cell(value: Any) -> str:
        return "null" if value is None else str(value).replace("|", "\\|")

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
                lines += ["Thinking:", "", *[f"> {line}" for line in summary.splitlines()], ""]
            elif kind_of == "text":
                lines += ["Text:", "", "```text", block.get("text", ""), "```", ""]
            elif kind_of == "tool_use":
                lines += [
                    f"Tool call `{block.get('name')}` (`{block.get('id')}`):",
                    "",
                    "```json",
                    json.dumps(block.get("input"), indent=2),
                    "```",
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
            "```text",
            event["result"],
            "```",
            "",
        ]
    if kind == "final_reply":
        verdict = "accepted" if event["accepted"] else f"rejected: {event['problem']}"
        return [f"**Final reply {verdict}.**", ""]
    if kind == "feedback":
        return ["Harness feedback sent to the model:", "", "```text", event["text"], "```", ""]
    if kind == "stopped":
        return [f"**Stopped:** {event['reason']}", ""]
    return [f"Event `{kind}`.", ""]
