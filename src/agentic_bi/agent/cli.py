"""Command line: ask one question live, or run a session of questions with one transcript each.

    uv run python -m agentic_bi.agent ask "<question>" [--out DIR]
    uv run python -m agentic_bi.agent session --questions FILE --out DIR

Both need ``ANTHROPIC_API_KEY`` in the environment and stop with a clear message before doing
anything when it is missing. The key is read by the SDK only; this module checks that it is
set and never prints it. The model is ``--model``, else ``AGENTIC_BI_MODEL``, else the default
of ``settings``. The warehouse is built into ``--database`` when the file is missing.

Exit codes: 0 when every question was answered or refused, 1 when one failed, 2 when nothing
could run (no key, a bad argument, an existing transcript, or an error every call would hit).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TextIO

from agentic_bi import warehouse
from agentic_bi.semantic import open_query_connection

from .answer import ANSWERED, FAILED, REFUSED, Answer
from .loop import AgentResult, ask
from .model import AnthropicModel, ModelClient, ModelError
from .settings import DEFAULT_MODEL, EFFORTS, MODEL_ENV, AgentSettings
from .tools import ToolBox
from .transcript import slug, write_transcript

KEY_ENV = "ANTHROPIC_API_KEY"
NO_KEY = (
    f"{KEY_ENV} is not set. The live agent calls the Anthropic API with your own key; nothing "
    "was sent. Set the key in your shell and run again. The offline replays need no key: "
    "uv run pytest tests/agent"
)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    model_factory: Callable[[], ModelClient] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the command line; ``model_factory`` stands in for the live model in tests."""
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr
    environ = os.environ if environ is None else environ
    args = _parser().parse_args(argv)

    if model_factory is None and not environ.get(KEY_ENV, "").strip():
        print(NO_KEY, file=stderr)
        return 2
    settings = AgentSettings.from_env(environ, model=args.model, effort=args.effort)

    if args.command == "ask":
        questions = [args.question.strip()]
        if not questions[0]:
            print("the question is empty", file=stderr)
            return 2
    else:
        try:
            questions = read_questions(args.questions)
        except (OSError, ValueError) as exc:
            print(f"cannot read the questions: {exc}", file=stderr)
            return 2
    stems = [f"{n:02d}-{slug(q)}" for n, q in enumerate(questions, start=1)]
    if args.out is not None:
        taken = [
            path
            for stem in stems
            for path in (args.out / f"{stem}.json", args.out / f"{stem}.md")
            if path.exists()
        ]
        if args.command == "session" and (args.out / "session.json").exists():
            taken.append(args.out / "session.json")
        if taken:
            print(f"{taken[0]} exists; choose another --out directory", file=stderr)
            return 2

    database = _database(args.database, stderr)
    connection = open_query_connection(database)
    try:
        toolbox = ToolBox(
            connection, max_rows=settings.max_rows, timeout_seconds=settings.timeout_seconds
        )
        model = model_factory() if model_factory is not None else AnthropicModel()
        if args.command == "ask":
            return _ask(questions[0], stems[0], args.out, model, toolbox, settings, stdout, stderr)
        return _session(questions, stems, args.out, model, toolbox, settings, stdout, stderr)
    finally:
        connection.close()


def read_questions(path: Path) -> list[str]:
    """One question per line; blank lines and lines starting with # are skipped."""
    lines = path.read_text(encoding="utf-8").splitlines()
    questions = [
        line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")
    ]
    if not questions:
        raise ValueError(f"{path} holds no question")
    return questions


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agentic_bi.agent",
        description="The analyst agent, live: it needs ANTHROPIC_API_KEY.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    one = commands.add_parser("ask", help="answer one question and print the evidence")
    one.add_argument("question")
    one.add_argument("--out", type=Path, help="also write the transcript into this directory")
    many = commands.add_parser("session", help="answer a list of questions, one transcript each")
    many.add_argument("--questions", type=Path, required=True, help="one question per line")
    many.add_argument("--out", type=Path, required=True, help="directory for the transcripts")
    for command in (one, many):
        command.add_argument(
            "--model", help=f"model id; default ${MODEL_ENV}, else {DEFAULT_MODEL}"
        )
        command.add_argument("--effort", choices=EFFORTS, help="output effort; default high")
        command.add_argument(
            "--database",
            type=Path,
            default=warehouse.DEFAULT_DATABASE,
            help="the warehouse file; built from the committed snapshot when missing",
        )
    return parser


def _database(path: Path, stderr: TextIO) -> Path:
    if not path.is_file():
        print(f"no warehouse at {path}; building it from the committed snapshot", file=stderr)
        warehouse.build(path)
    return path


def _ask(question, stem, out, model, toolbox, settings, stdout, stderr) -> int:
    try:
        result = ask(question, model=model, toolbox=toolbox, settings=settings)
    except ModelError as exc:
        print(f"stopped: {exc}", file=stderr)
        return 2
    print(describe(result.answer), file=stdout)
    if out is not None:
        paths = write_transcript(out, stem, result.transcript)
        print(f"\nTranscript: {paths[0]} and {paths[1].name}", file=stdout)
    return 1 if result.answer.status == FAILED else 0


def _session(questions, stems, out, model, toolbox, settings, stdout, stderr) -> int:
    index: list[dict[str, object]] = []
    code = 0
    for number, (question, stem) in enumerate(zip(questions, stems, strict=True), start=1):
        try:
            result: AgentResult = ask(question, model=model, toolbox=toolbox, settings=settings)
        except ModelError as exc:
            print(f"[{number}/{len(questions)}] stopped: {exc}", file=stderr)
            _write_index(out, index, stopped=str(exc))
            return 2
        write_transcript(out, stem, result.transcript)
        answer = result.answer
        index.append(
            {
                "number": number,
                "question": question,
                "status": answer.status,
                "transcript": f"{stem}.md",
                "model": answer.model,
                "model_calls": answer.model_calls,
                "tool_calls": answer.tool_calls,
                "usage": result.transcript["usage"],
            }
        )
        print(f"[{number}/{len(questions)}] {answer.status}: {question}", file=stdout)
        if answer.status == FAILED:
            code = 1
    _write_index(out, index, stopped=None)
    print(f"Transcripts in {out}; start with {out / 'session.md'}", file=stdout)
    return code


def _write_index(out: Path, index: list[dict[str, object]], stopped: str | None) -> None:
    out.mkdir(parents=True, exist_ok=True)
    session = {"questions": index, "stopped": stopped}
    (out / "session.json").write_text(json.dumps(session, indent=2) + "\n", encoding="utf-8")
    lines = ["# Session", ""]
    if stopped:
        lines += [f"Stopped early: {stopped}", ""]
    lines += ["| # | Status | Tool calls | Question |", "| --- | --- | --- | --- |"]
    for item in index:
        link = f"[{item['question']}]({item['transcript']})"
        lines.append(f"| {item['number']} | {item['status']} | {item['tool_calls']} | {link} |")
    (out / "session.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def describe(answer: Answer) -> str:
    """The answer and its evidence, for a terminal."""
    lines = [f"Question: {answer.question}"]
    if answer.status == ANSWERED:
        lines.append(f"Answer: {answer.text}")
    elif answer.status == REFUSED:
        lines.append(f"Refused ({answer.refusal.code}): {answer.refusal.reason}")
    else:
        lines.append(f"Failed: {answer.failure}")
    for number, run in enumerate(answer.queries, start=1):
        data = run.to_dict()
        lines += [
            "",
            f"Query {number} of {len(answer.queries)}: {json.dumps(data['query'])}",
            "SQL:",
            *[f"    {line}" for line in run.sql.splitlines()],
            f"Parameters: {json.dumps(data['params'])}",
            f"Rows ({len(run.rows)}): {', '.join(run.columns)}",
            *[f"    {json.dumps(row)}" for row in data["rows"][:20]],
        ]
        if len(run.rows) > 20:
            lines.append(f"    ... {len(run.rows) - 20} more rows in the transcript")
    lines += [
        "",
        f"Model: {answer.model}; {answer.model_calls} model calls, {answer.tool_calls} tool calls",
    ]
    return "\n".join(lines)
