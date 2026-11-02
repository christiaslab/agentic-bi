"""The command line: the key check first, the exit codes, and the files it writes.

Nothing here calls the network. Every run that gets as far as a model gets a replay or a stand-in
through ``model_factory``; the live model class and the warehouse build are swapped for guards that
fail a test reaching them by accident. The only key-like value is built by concatenation.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import anthropic
import pytest

from agentic_bi.agent import cli
from agentic_bi.agent.answer import ANSWERED, FAILED, REFUSED, Answer, QueryRun, Refusal
from agentic_bi.agent.cli import KEY_ENV, NO_KEY, describe, main, read_questions
from agentic_bi.agent.model import (
    REPLAY_MODEL,
    REPLAY_PROVENANCE,
    ModelClient,
    ModelError,
    ReplayModel,
)
from agentic_bi.agent.settings import DEFAULT_MODEL, MODEL_ENV
from agentic_bi.semantic import compile as compile_query

REPO_ROOT = Path(__file__).resolve().parents[2]
KEY = "placeholder-" + "not-a-key"
QUESTION = "What were obligations?"
SLUG = "what-were-obligations"
SECOND_QUESTION = "How many awards were active?"
SECOND_SLUG = "how-many-awards-were-active"
FISCAL_YEAR = {"start": "2024-10-01", "end": "2025-09-30"}


@pytest.fixture(autouse=True)
def no_live_client_and_no_real_warehouse(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test that reaches the live SDK client or builds the real warehouse fails at once."""

    def refuse_client(*args: object, **kwargs: object) -> None:
        raise AssertionError("a test must never build the live client")

    def refuse_build(*args: object, **kwargs: object) -> None:
        raise AssertionError("a test must never build the real warehouse")

    monkeypatch.setattr(anthropic, "Anthropic", refuse_client)
    monkeypatch.setattr(cli.warehouse, "build", refuse_build)


# --- hand-written turns and stand-in models ---------------------------------------------------


def use_tool(call_id: str, name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    block = {"type": "tool_use", "id": call_id, "name": name, "input": tool_input}
    return {"stop_reason": "tool_use", "content": [block]}


def reply(text: str) -> dict[str, Any]:
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}


def answered(text: str) -> dict[str, Any]:
    return reply(json.dumps({"status": "answered", "answer": text}))


def refused(code: str, reason: str) -> dict[str, Any]:
    return reply(json.dumps({"status": "refused", "code": code, "reason": reason}))


# The synthetic schema: net obligations 3600.00 over 6 awards.
OBLIGATIONS_TEXT = "Net obligations were $3,600.00."
AWARDS_TEXT = "There were 6 awards active in the period."
OBLIGATIONS = [
    use_tool("toolu_1", "run_query", {"metric": "obligations"}),
    answered(OBLIGATIONS_TEXT),
]
AWARDS = [
    use_tool("toolu_2", "run_query", {"metric": "award_count"}),
    answered(AWARDS_TEXT),
]
NOT_JSON = [reply("About 3600 dollars."), reply("Still not a JSON object.")]
REFUSAL = [refused("outside_coverage", "The warehouse holds one agency and one fiscal year only.")]


class Serving:
    """A model factory that hands out one model and counts how often it was asked for it."""

    def __init__(self, model: ModelClient) -> None:
        self.model = model
        self.calls = 0

    def __call__(self) -> ModelClient:
        self.calls += 1
        return self.model


def refuse_to_build() -> ModelClient:
    raise AssertionError("the model factory must not be called")


class Failing:
    """A model whose every call raises ``error``."""

    provenance = "test: a model call that fails"

    def __init__(self, error: ModelError) -> None:
        self.error = error

    def create(self, **_: Any) -> Any:
        raise self.error


class OnePerQuestion:
    """Serves a session: each question has its own ``ReplayModel`` (or the error it raises).

    One ``ReplayModel`` counts its turns across questions, so it cannot serve a second question;
    this stand-in starts the next replay whenever the loop opens a conversation, which is a request
    that holds the question alone. Every conversation is still checked by a real ``ReplayModel``.
    """

    provenance = REPLAY_PROVENANCE

    def __init__(self, *per_question: list[dict[str, Any]] | ModelError) -> None:
        self.handlers = [
            handler if isinstance(handler, ModelError) else ReplayModel(handler)
            for handler in per_question
        ]
        self.current = -1

    def create(self, *, messages: list[dict[str, Any]], **given: Any) -> Any:
        if len(messages) == 1:
            self.current += 1
        handler = self.handlers[self.current]
        if isinstance(handler, ModelError):
            raise handler
        return handler.create(messages=messages, **given)


# --- running main -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Run:
    code: int
    stdout: str
    stderr: str


def run_main(
    *argv: str,
    environ: Mapping[str, str] | None = None,
    model_factory: Callable[[], ModelClient] | None = None,
) -> Run:
    stdout, stderr = io.StringIO(), io.StringIO()
    code = main(
        list(argv),
        environ={} if environ is None else environ,
        model_factory=model_factory,
        stdout=stdout,
        stderr=stderr,
    )
    return Run(code, stdout.getvalue(), stderr.getvalue())


def write_questions(directory: Path, *lines: str) -> Path:
    path = directory / "questions.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def snapshot(directory: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(directory.iterdir())}


# --- no key: stop before anything else --------------------------------------------------------

NO_KEY_ENVIRONS = [
    pytest.param({}, id="absent"),
    pytest.param({KEY_ENV: ""}, id="empty"),
    pytest.param({KEY_ENV: "   "}, id="spaces"),
    pytest.param({KEY_ENV: "\t\n"}, id="whitespace"),
]


def test_the_no_key_message_names_the_variable_and_says_nothing_was_sent() -> None:
    assert KEY_ENV == "ANTHROPIC_API_KEY"
    assert NO_KEY.startswith("ANTHROPIC_API_KEY is not set")
    assert "nothing was sent" in NO_KEY


@pytest.mark.parametrize("environ", NO_KEY_ENVIRONS)
def test_ask_without_a_key_stops_before_it_touches_anything(
    tmp_path: Path, environ: Mapping[str, str]
) -> None:
    database = tmp_path / "warehouse" / "missing.duckdb"
    out = tmp_path / "transcripts"
    run = run_main("ask", QUESTION, "--database", str(database), "--out", str(out), environ=environ)
    assert run.code == 2
    assert run.stderr == NO_KEY + "\n"
    assert run.stdout == ""
    assert not database.exists() and not database.parent.exists()
    assert not out.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("environ", NO_KEY_ENVIRONS)
def test_session_without_a_key_stops_before_it_touches_anything(
    tmp_path: Path, environ: Mapping[str, str]
) -> None:
    questions = write_questions(tmp_path, QUESTION)
    database = tmp_path / "warehouse" / "missing.duckdb"
    out = tmp_path / "transcripts"
    run = run_main(
        "session",
        "--questions",
        str(questions),
        "--out",
        str(out),
        "--database",
        str(database),
        environ=environ,
    )
    assert run.code == 2
    assert run.stderr == NO_KEY + "\n"
    assert run.stdout == ""
    assert not database.exists() and not database.parent.exists()
    assert not out.exists()
    assert [path.name for path in tmp_path.iterdir()] == ["questions.txt"]


def test_the_key_is_checked_before_the_questions_file_is_read(tmp_path: Path) -> None:
    run = run_main(
        "session",
        "--questions",
        str(tmp_path / "absent.txt"),
        "--out",
        str(tmp_path / "out"),
        "--database",
        str(tmp_path / "missing.duckdb"),
    )
    assert run.code == 2 and run.stderr == NO_KEY + "\n"


def test_the_default_streams_are_the_process_streams(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["ask", QUESTION], environ={}) == 2
    captured = capsys.readouterr()
    assert captured.err == NO_KEY + "\n" and captured.out == ""


@pytest.mark.parametrize("command", ["ask", "session"])
def test_the_module_stops_on_a_missing_key_before_building_or_writing_anything(
    tmp_path: Path, command: str
) -> None:
    database = tmp_path / "missing.duckdb"
    out = tmp_path / "out"
    questions = write_questions(tmp_path, QUESTION)
    arguments = (
        ["ask", QUESTION]
        if command == "ask"
        else ["session", "--questions", str(questions), "--out", str(out)]
    )
    environment = {name: value for name, value in os.environ.items() if name != KEY_ENV}
    completed = subprocess.run(
        [sys.executable, "-m", "agentic_bi.agent", *arguments, "--database", str(database)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=environment,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 2
    assert "ANTHROPIC_API_KEY is not set" in completed.stderr
    assert completed.stdout == ""
    assert not database.exists() and not out.exists()


# --- the command line itself ------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["unknown"],
        ["ask"],
        ["session", "--out", "out"],
        ["session", "--questions", "questions.txt"],
        ["ask", QUESTION, "--effort", "turbo"],
    ],
    ids=["no-command", "unknown-command", "no-question", "no-questions", "no-out", "bad-effort"],
)
def test_a_bad_command_line_is_refused_by_the_parser_with_exit_code_two(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as stop:
        main(argv, environ={})
    assert stop.value.code == 2
    assert "usage:" in capsys.readouterr().err


def test_an_empty_question_is_refused_before_any_work(tmp_path: Path) -> None:
    database = tmp_path / "missing.duckdb"
    run = run_main("ask", "   ", "--database", str(database), model_factory=refuse_to_build)
    assert run.code == 2
    assert run.stderr == "the question is empty\n" and run.stdout == ""
    assert not database.exists()


def test_the_question_is_stripped_of_surrounding_whitespace(synthetic_db: Path) -> None:
    run = run_main(
        "ask",
        f"   {QUESTION}  ",
        "--database",
        str(synthetic_db),
        model_factory=Serving(ReplayModel(OBLIGATIONS)),
    )
    assert run.stdout.startswith(f"Question: {QUESTION}\n")


# --- read_questions ---------------------------------------------------------------------------


def test_read_questions_skips_blank_lines_and_comments_and_strips_whitespace(
    tmp_path: Path,
) -> None:
    path = tmp_path / "questions.txt"
    path.write_text(
        "# the questions of a session\n"
        "\n"
        f"  {QUESTION}  \n"
        "   # an indented comment\n"
        "\t\n"
        f"{SECOND_QUESTION}\n",
        encoding="utf-8",
    )
    assert read_questions(path) == [QUESTION, SECOND_QUESTION]


def test_read_questions_keeps_a_hash_that_does_not_start_the_line(tmp_path: Path) -> None:
    path = tmp_path / "questions.txt"
    path.write_text("What did NAICS #562910 receive?\n", encoding="utf-8")
    assert read_questions(path) == ["What did NAICS #562910 receive?"]


def test_read_questions_reads_windows_line_endings(tmp_path: Path) -> None:
    path = tmp_path / "questions.txt"
    path.write_bytes(f"{QUESTION}\r\n\r\n{SECOND_QUESTION}\r\n".encode())
    assert read_questions(path) == [QUESTION, SECOND_QUESTION]


@pytest.mark.parametrize(
    "content",
    ["", "\n\n", "# only a comment\n   \n  # another\n"],
    ids=["empty", "blank-lines", "only-comments"],
)
def test_a_questions_file_with_no_question_raises_a_value_error(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "questions.txt"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="holds no question"):
        read_questions(path)


def test_a_missing_questions_file_raises_an_os_error(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        read_questions(tmp_path / "absent.txt")


def make_absent(directory: Path) -> Path:
    return directory / "absent.txt"


def make_directory(directory: Path) -> Path:
    (directory / "a-directory").mkdir()
    return directory / "a-directory"


def make_empty(directory: Path) -> Path:
    path = directory / "questions.txt"
    path.write_text("", encoding="utf-8")
    return path


def make_only_comments(directory: Path) -> Path:
    path = directory / "questions.txt"
    path.write_text("# nothing to ask\n\n", encoding="utf-8")
    return path


def make_not_text(directory: Path) -> Path:
    path = directory / "questions.txt"
    path.write_bytes(b"\xff\xfe\x00 not utf-8")
    return path


@pytest.mark.parametrize(
    "make",
    [make_absent, make_directory, make_empty, make_only_comments, make_not_text],
    ids=lambda make: make.__name__.removeprefix("make_"),
)
def test_session_with_a_questions_file_that_cannot_give_a_question_returns_two(
    tmp_path: Path, make: Callable[[Path], Path]
) -> None:
    questions = make(tmp_path)
    database = tmp_path / "missing.duckdb"
    out = tmp_path / "out"
    run = run_main(
        "session",
        "--questions",
        str(questions),
        "--out",
        str(out),
        "--database",
        str(database),
        model_factory=refuse_to_build,
    )
    assert run.code == 2
    assert run.stdout == ""
    assert run.stderr.startswith("cannot read the questions: ")
    assert not database.exists() and not out.exists()


def test_a_questions_file_with_no_question_is_named_in_the_message(tmp_path: Path) -> None:
    questions = make_only_comments(tmp_path)
    run = run_main(
        "session",
        "--questions",
        str(questions),
        "--out",
        str(tmp_path / "out"),
        "--database",
        str(tmp_path / "missing.duckdb"),
        model_factory=refuse_to_build,
    )
    assert run.stderr == f"cannot read the questions: {questions} holds no question\n"


# --- ask: the answer and its evidence ---------------------------------------------------------


def test_ask_needs_no_key_when_a_model_factory_is_given(synthetic_db: Path) -> None:
    factory = Serving(ReplayModel(OBLIGATIONS))
    run = run_main("ask", QUESTION, "--database", str(synthetic_db), model_factory=factory)
    assert run.code == 0 and run.stderr == ""
    assert factory.calls == 1


def test_ask_prints_the_answer_the_sql_and_the_parameters(synthetic_db: Path) -> None:
    replay = ReplayModel(OBLIGATIONS)
    run = run_main("ask", QUESTION, "--database", str(synthetic_db), model_factory=Serving(replay))
    assert run.code == 0
    assert run.stderr == ""
    assert replay.exhausted
    compiled = compile_query({"metric": "obligations"})
    sql = "\n".join(f"    {line}" for line in compiled.sql.splitlines())
    assert run.stdout.startswith(f"Question: {QUESTION}\nAnswer: Net obligations were $3,600.00.\n")
    assert f"SQL:\n{sql}\n" in run.stdout
    assert "Parameters: []\n" in run.stdout
    assert 'Rows (1): obligations\n    ["3600.00"]\n' in run.stdout
    assert f"Model: {REPLAY_MODEL}; 2 model calls, 1 tool calls\n" in run.stdout


def test_ask_prints_the_bound_values_as_parameters_and_never_inside_the_sql(
    synthetic_db: Path,
) -> None:
    query = {"metric": "obligations", "time_range": FISCAL_YEAR}
    turns = [use_tool("toolu_1", "run_query", query), OBLIGATIONS[1]]
    run = run_main(
        "ask", QUESTION, "--database", str(synthetic_db), model_factory=Serving(ReplayModel(turns))
    )
    assert run.code == 0
    assert 'Parameters: ["2024-10-01", "2025-09-30"]\n' in run.stdout
    sql = run.stdout.split("SQL:\n")[1].split("Parameters:")[0]
    assert "?" in sql and "2024-10-01" not in sql and "2025-09-30" not in sql


def test_ask_without_out_writes_no_file(
    tmp_path: Path, synthetic_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    run = run_main(
        "ask",
        QUESTION,
        "--database",
        str(synthetic_db),
        model_factory=Serving(ReplayModel(OBLIGATIONS)),
    )
    assert run.code == 0 and "Transcript:" not in run.stdout
    assert list(tmp_path.iterdir()) == []


def test_ask_with_out_writes_a_json_and_a_markdown_transcript_that_say_replay(
    tmp_path: Path, synthetic_db: Path
) -> None:
    out = tmp_path / "transcripts"
    run = run_main(
        "ask",
        QUESTION,
        "--database",
        str(synthetic_db),
        "--out",
        str(out),
        model_factory=Serving(ReplayModel(OBLIGATIONS)),
    )
    assert run.code == 0
    assert sorted(path.name for path in out.iterdir()) == [f"01-{SLUG}.json", f"01-{SLUG}.md"]
    assert f"Transcript: {out / f'01-{SLUG}.json'} and 01-{SLUG}.md\n" in run.stdout
    transcript = read_json(out / f"01-{SLUG}.json")
    assert transcript["question"] == QUESTION
    assert transcript["provenance"] == REPLAY_PROVENANCE
    assert transcript["provenance"].startswith("replay")
    assert transcript["answer"]["status"] == "answered"
    assert transcript["answer"]["model"] == REPLAY_MODEL
    markdown = (out / f"01-{SLUG}.md").read_text(encoding="utf-8")
    assert markdown.startswith(f"# {QUESTION}\n")
    assert f"- Provenance: {REPLAY_PROVENANCE}\n" in markdown


@pytest.mark.parametrize(
    ("question", "stem"),
    [
        (
            "  Top 5 recipients by obligations, FY2025!  ",
            "01-top-5-recipients-by-obligations-fy2025",
        ),
        ("???", "01-question"),
        ("hello " * 12, "01-" + "-".join(["hello"] * 8)),
    ],
    ids=["punctuation", "no-words", "cut-at-a-word-end"],
)
def test_ask_names_the_transcript_after_the_numbered_slug_of_the_question(
    tmp_path: Path, synthetic_db: Path, question: str, stem: str
) -> None:
    out = tmp_path / "transcripts"
    run = run_main(
        "ask",
        question,
        "--database",
        str(synthetic_db),
        "--out",
        str(out),
        model_factory=Serving(ReplayModel(REFUSAL)),
    )
    assert run.code == 0
    assert sorted(path.name for path in out.iterdir()) == [f"{stem}.json", f"{stem}.md"]


def test_a_failed_run_returns_one_and_still_writes_its_transcript(
    tmp_path: Path, synthetic_db: Path
) -> None:
    out = tmp_path / "transcripts"
    replay = ReplayModel(NOT_JSON)
    run = run_main(
        "ask",
        QUESTION,
        "--database",
        str(synthetic_db),
        "--out",
        str(out),
        model_factory=Serving(replay),
    )
    assert run.code == 1
    assert replay.exhausted
    problem = "the final reply must be exactly one JSON object"
    assert f"Failed: final reply rejected: {problem}\n" in run.stdout
    assert "Answer:" not in run.stdout
    transcript = read_json(out / f"01-{SLUG}.json")
    assert transcript["answer"]["status"] == "failed"
    assert transcript["provenance"] == REPLAY_PROVENANCE


def test_a_refusal_returns_zero_and_shows_its_code(synthetic_db: Path) -> None:
    run = run_main(
        "ask",
        "How much did NASA spend?",
        "--database",
        str(synthetic_db),
        model_factory=Serving(ReplayModel(REFUSAL)),
    )
    assert run.code == 0
    reason = "The warehouse holds one agency and one fiscal year only."
    assert f"Refused (outside_coverage): {reason}\n" in run.stdout


def test_a_fatal_model_error_returns_two_and_writes_nothing(
    tmp_path: Path, synthetic_db: Path
) -> None:
    error = ModelError("the API refused the key: check ANTHROPIC_API_KEY", fatal=True)
    out = tmp_path / "transcripts"
    run = run_main(
        "ask",
        QUESTION,
        "--database",
        str(synthetic_db),
        "--out",
        str(out),
        model_factory=Serving(Failing(error)),
    )
    assert run.code == 2
    assert run.stdout == ""
    assert run.stderr == f"stopped: {error}\n"
    assert not out.exists()


def test_a_model_error_that_is_not_fatal_fails_the_question_with_exit_code_one(
    tmp_path: Path, synthetic_db: Path
) -> None:
    error = ModelError("rate limited after the SDK's retries", fatal=False)
    out = tmp_path / "transcripts"
    run = run_main(
        "ask",
        QUESTION,
        "--database",
        str(synthetic_db),
        "--out",
        str(out),
        model_factory=Serving(Failing(error)),
    )
    assert run.code == 1
    assert f"Failed: model call failed: {error}\n" in run.stdout
    assert (out / f"01-{SLUG}.json").is_file()


def test_ask_into_an_existing_transcript_returns_two_and_leaves_it_alone(
    tmp_path: Path, synthetic_db: Path
) -> None:
    out = tmp_path / "transcripts"
    out.mkdir()
    existing = out / f"01-{SLUG}.md"
    existing.write_text("an earlier run\n", encoding="utf-8")
    run = run_main(
        "ask",
        QUESTION,
        "--database",
        str(synthetic_db),
        "--out",
        str(out),
        model_factory=refuse_to_build,
    )
    assert run.code == 2
    assert run.stdout == ""
    assert run.stderr == f"{existing} exists; choose another --out directory\n"
    assert snapshot(out) == {existing.name: b"an earlier run\n"}


# --- the model, the effort and the key reach the right place ----------------------------------


@pytest.mark.parametrize(
    ("flags", "environ", "model", "effort"),
    [
        ([], {}, DEFAULT_MODEL, "high"),
        ([], {MODEL_ENV: "env-model-id"}, "env-model-id", "high"),
        ([], {MODEL_ENV: "  env-model-id  "}, "env-model-id", "high"),
        (["--model", "flag-model-id"], {MODEL_ENV: "env-model-id"}, "flag-model-id", "high"),
        (["--effort", "max"], {}, DEFAULT_MODEL, "max"),
        (["--model", "flag-model-id", "--effort", "low"], {}, "flag-model-id", "low"),
    ],
    ids=["defaults", "env", "env-stripped", "flag-beats-env", "effort", "model-and-effort"],
)
def test_the_model_and_effort_come_from_the_flags_then_the_environment_then_the_defaults(
    tmp_path: Path,
    synthetic_db: Path,
    flags: list[str],
    environ: dict[str, str],
    model: str,
    effort: str,
) -> None:
    replay = ReplayModel(REFUSAL)
    out = tmp_path / "transcripts"
    run = run_main(
        "ask",
        QUESTION,
        "--database",
        str(synthetic_db),
        "--out",
        str(out),
        *flags,
        environ=environ,
        model_factory=Serving(replay),
    )
    assert run.code == 0
    request = replay.requests[0]
    assert request["model"] == model
    assert request["output_config"] == {"effort": effort}
    settings = read_json(out / f"01-{SLUG}.json")["settings"]
    assert settings["model"] == model and settings["effort"] == effort


@pytest.mark.parametrize("value", ["", "   "], ids=["empty", "spaces"])
def test_an_empty_model_flag_is_a_bad_argument_with_exit_code_two(
    synthetic_db: Path, value: str
) -> None:
    run = run_main(
        "ask",
        QUESTION,
        "--database",
        str(synthetic_db),
        "--model",
        value,
        model_factory=refuse_to_build,
    )
    assert run.code == 2
    assert run.stdout == "" and "model" in run.stderr


def test_with_a_key_and_no_factory_the_cli_builds_the_live_model_class(
    tmp_path: Path, synthetic_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replay = ReplayModel(OBLIGATIONS)
    built: list[int] = []

    def live_model() -> ReplayModel:
        built.append(1)
        return replay

    monkeypatch.setattr(cli, "AnthropicModel", live_model)
    out = tmp_path / "transcripts"
    run = run_main(
        "ask",
        QUESTION,
        "--database",
        str(synthetic_db),
        "--out",
        str(out),
        environ={KEY_ENV: KEY},
    )
    assert run.code == 0 and built == [1]
    everything = (
        run.stdout
        + run.stderr
        + "".join(path.read_text(encoding="utf-8") for path in sorted(out.iterdir()))
    )
    assert KEY not in everything


def test_the_key_value_is_never_printed_or_recorded_by_a_run_with_a_factory(
    tmp_path: Path, synthetic_db: Path
) -> None:
    out = tmp_path / "transcripts"
    run = run_main(
        "ask",
        QUESTION,
        "--database",
        str(synthetic_db),
        "--out",
        str(out),
        environ={KEY_ENV: KEY},
        model_factory=Serving(ReplayModel(OBLIGATIONS)),
    )
    everything = (
        run.stdout
        + run.stderr
        + "".join(path.read_text(encoding="utf-8") for path in sorted(out.iterdir()))
    )
    assert run.code == 0 and KEY not in everything


# --- the warehouse file -----------------------------------------------------------------------


def test_a_missing_database_is_built_from_the_snapshot_before_the_run(
    tmp_path: Path, synthetic_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "data" / "warehouse.duckdb"
    target.parent.mkdir()
    built: list[Path] = []

    def build_stand_in(path: Path) -> Path:
        built.append(Path(path))
        shutil.copyfile(synthetic_db, path)
        return Path(path)

    monkeypatch.setattr(cli.warehouse, "build", build_stand_in)
    run = run_main(
        "ask", QUESTION, "--database", str(target), model_factory=Serving(ReplayModel(OBLIGATIONS))
    )
    assert run.code == 0
    assert built == [target]
    assert run.stderr == f"no warehouse at {target}; building it from the committed snapshot\n"


def test_an_existing_database_is_used_as_it_is(synthetic_db: Path) -> None:
    run = run_main(
        "ask",
        QUESTION,
        "--database",
        str(synthetic_db),
        model_factory=Serving(ReplayModel(OBLIGATIONS)),
    )
    assert run.code == 0 and run.stderr == ""


# --- session ----------------------------------------------------------------------------------


def run_session(
    tmp_path: Path, database: Path, factory: Callable[[], ModelClient], *lines: str
) -> tuple[Run, Path]:
    """Run a session on a questions file written from ``lines``; return the run and --out."""
    questions = write_questions(tmp_path, *lines)
    out = tmp_path / "transcripts"
    run = run_main(
        "session",
        "--questions",
        str(questions),
        "--out",
        str(out),
        "--database",
        str(database),
        model_factory=factory,
    )
    return run, out


def test_session_writes_a_transcript_pair_per_question_and_an_index(
    tmp_path: Path, synthetic_db: Path
) -> None:
    model = OnePerQuestion(OBLIGATIONS, AWARDS)
    factory = Serving(model)
    questions = write_questions(tmp_path, "# two questions", QUESTION, "", SECOND_QUESTION)
    out = tmp_path / "transcripts"
    run = run_main(
        "session",
        "--questions",
        str(questions),
        "--out",
        str(out),
        "--database",
        str(synthetic_db),
        model_factory=factory,
    )
    assert run.code == 0
    assert run.stderr == ""
    assert factory.calls == 1
    assert all(replay.exhausted for replay in model.handlers)
    assert sorted(path.name for path in out.iterdir()) == [
        f"01-{SLUG}.json",
        f"01-{SLUG}.md",
        f"02-{SECOND_SLUG}.json",
        f"02-{SECOND_SLUG}.md",
        "session.json",
        "session.md",
    ]
    assert run.stdout == (
        f"[1/2] answered: {QUESTION}\n"
        f"[2/2] answered: {SECOND_QUESTION}\n"
        f"Transcripts in {out}; start with {out / 'session.md'}\n"
    )
    first = read_json(out / f"01-{SLUG}.json")
    second = read_json(out / f"02-{SECOND_SLUG}.json")
    assert (first["question"], first["answer"]["text"]) == (QUESTION, OBLIGATIONS_TEXT)
    assert (second["question"], second["answer"]["text"]) == (SECOND_QUESTION, AWARDS_TEXT)
    assert first["provenance"] == second["provenance"] == REPLAY_PROVENANCE


def test_session_index_lists_each_question_with_its_status_and_counts(
    tmp_path: Path, synthetic_db: Path
) -> None:
    run, out = run_session(
        tmp_path,
        synthetic_db,
        Serving(OnePerQuestion(OBLIGATIONS, AWARDS)),
        QUESTION,
        SECOND_QUESTION,
    )
    assert run.code == 0

    def entry(number: int, question: str, slug: str) -> dict[str, Any]:
        return {
            "number": number,
            "question": question,
            "status": "answered",
            "transcript": f"{number:02d}-{slug}.md",
            "model": REPLAY_MODEL,
            "model_calls": 2,
            "tool_calls": 1,
            "usage": {},
        }

    assert read_json(out / "session.json") == {
        "questions": [entry(1, QUESTION, SLUG), entry(2, SECOND_QUESTION, SECOND_SLUG)],
        "stopped": None,
    }
    assert (out / "session.md").read_text(encoding="utf-8") == (
        "# Session\n"
        "\n"
        "| # | Status | Tool calls | Question |\n"
        "| --- | --- | --- | --- |\n"
        f"| 1 | answered | 1 | [{QUESTION}](01-{SLUG}.md) |\n"
        f"| 2 | answered | 1 | [{SECOND_QUESTION}](02-{SECOND_SLUG}.md) |\n"
    )


def test_a_second_session_into_the_same_directory_returns_two_before_calling_the_model(
    tmp_path: Path, synthetic_db: Path
) -> None:
    first, out = run_session(
        tmp_path,
        synthetic_db,
        Serving(OnePerQuestion(OBLIGATIONS, AWARDS)),
        QUESTION,
        SECOND_QUESTION,
    )
    assert first.code == 0
    before = snapshot(out)
    database = tmp_path / "never-built.duckdb"
    run = run_main(
        "session",
        "--questions",
        str(tmp_path / "questions.txt"),
        "--out",
        str(out),
        "--database",
        str(database),
        model_factory=refuse_to_build,
    )
    assert run.code == 2
    assert run.stdout == ""
    assert run.stderr == f"{out / f'01-{SLUG}.json'} exists; choose another --out directory\n"
    assert snapshot(out) == before
    assert not database.exists()


def test_session_checks_every_transcript_path_before_it_runs_any_question(
    tmp_path: Path, synthetic_db: Path
) -> None:
    out = tmp_path / "transcripts"
    out.mkdir()
    taken = out / f"02-{SECOND_SLUG}.md"
    taken.write_text("an earlier run\n", encoding="utf-8")
    run, _ = run_session(tmp_path, synthetic_db, refuse_to_build, QUESTION, SECOND_QUESTION)
    assert run.code == 2
    assert run.stderr == f"{taken} exists; choose another --out directory\n"
    assert snapshot(out) == {taken.name: b"an earlier run\n"}


def test_session_refuses_a_directory_that_already_holds_an_index(
    tmp_path: Path, synthetic_db: Path
) -> None:
    out = tmp_path / "transcripts"
    out.mkdir()
    (out / "session.json").write_text("{}\n", encoding="utf-8")
    run, _ = run_session(tmp_path, synthetic_db, refuse_to_build, QUESTION)
    assert run.code == 2
    assert run.stderr == f"{out / 'session.json'} exists; choose another --out directory\n"
    assert snapshot(out) == {"session.json": b"{}\n"}


def test_a_failed_question_makes_the_session_return_one_but_the_next_one_still_runs(
    tmp_path: Path, synthetic_db: Path
) -> None:
    model = OnePerQuestion(NOT_JSON, AWARDS)
    run, out = run_session(tmp_path, synthetic_db, Serving(model), QUESTION, SECOND_QUESTION)
    assert run.code == 1
    assert run.stdout.splitlines()[:2] == [
        f"[1/2] failed: {QUESTION}",
        f"[2/2] answered: {SECOND_QUESTION}",
    ]
    index = read_json(out / "session.json")
    assert [item["status"] for item in index["questions"]] == ["failed", "answered"]
    assert index["stopped"] is None
    assert (out / f"01-{SLUG}.json").is_file() and (out / f"02-{SECOND_SLUG}.json").is_file()


def test_a_refused_question_does_not_make_the_session_fail(
    tmp_path: Path, synthetic_db: Path
) -> None:
    model = OnePerQuestion(REFUSAL, OBLIGATIONS)
    run, out = run_session(
        tmp_path, synthetic_db, Serving(model), "How much did NASA spend?", QUESTION
    )
    assert run.code == 0
    index = read_json(out / "session.json")
    assert [item["status"] for item in index["questions"]] == ["refused", "answered"]


def test_a_fatal_error_stops_the_session_and_the_index_says_why(
    tmp_path: Path, synthetic_db: Path
) -> None:
    error = ModelError("the API refused the key: check ANTHROPIC_API_KEY", fatal=True)
    model = OnePerQuestion(OBLIGATIONS, error)
    run, out = run_session(tmp_path, synthetic_db, Serving(model), QUESTION, SECOND_QUESTION)
    assert run.code == 2
    assert run.stderr == f"[2/2] stopped: {error}\n"
    assert run.stdout == f"[1/2] answered: {QUESTION}\n"
    assert sorted(path.name for path in out.iterdir()) == [
        f"01-{SLUG}.json",
        f"01-{SLUG}.md",
        "session.json",
        "session.md",
    ]
    index = read_json(out / "session.json")
    assert index["stopped"] == str(error)
    assert [item["number"] for item in index["questions"]] == [1]
    assert f"Stopped early: {error}\n" in (out / "session.md").read_text(encoding="utf-8")


def test_a_session_can_run_on_one_replay_model_whose_turns_cover_both_questions(
    tmp_path: Path, synthetic_db: Path
) -> None:
    replay = ReplayModel([*OBLIGATIONS, *AWARDS])
    run, out = run_session(tmp_path, synthetic_db, Serving(replay), QUESTION, SECOND_QUESTION)
    assert run.code == 0
    assert replay.exhausted
    assert (out / f"02-{SECOND_SLUG}.json").is_file() and (out / "session.json").is_file()


# --- describe ---------------------------------------------------------------------------------

MONTHLY = QueryRun(
    query={"metric": "obligations", "grain": "fiscal_month"},
    sql='SELECT 1\nFROM "t"',
    params=(dt.date(2024, 10, 1), dt.date(2025, 9, 30)),
    columns=("fiscal_month", "obligations"),
    rows=tuple((number, Decimal(f"{number}.50")) for number in range(1, 4)),
)


def run_with_rows(count: int) -> QueryRun:
    rows = tuple((number, Decimal(f"{number}.50")) for number in range(1, count + 1))
    return QueryRun(
        query={"metric": "obligations"}, sql="SELECT 1", params=(), columns=("m", "o"), rows=rows
    )


def test_describe_shows_the_answer_the_query_its_sql_parameters_and_rows() -> None:
    answer = Answer(
        question="Q?",
        status=ANSWERED,
        text="Net obligations rose.",
        queries=(MONTHLY,),
        model="model-a",
        model_calls=3,
        tool_calls=2,
    )
    assert describe(answer) == "\n".join(
        [
            "Question: Q?",
            "Answer: Net obligations rose.",
            "",
            'Query 1 of 1: {"metric": "obligations", "grain": "fiscal_month"}',
            "SQL:",
            "    SELECT 1",
            '    FROM "t"',
            'Parameters: ["2024-10-01", "2025-09-30"]',
            "Rows (3): fiscal_month, obligations",
            '    [1, "1.50"]',
            '    [2, "2.50"]',
            '    [3, "3.50"]',
            "",
            "Model: model-a; 3 model calls, 2 tool calls",
        ]
    )


def test_describe_numbers_every_query_of_an_answer() -> None:
    answer = Answer(question="Q?", status=ANSWERED, text="Done.", queries=(MONTHLY, MONTHLY))
    lines = describe(answer).splitlines()
    assert [line for line in lines if line.startswith("Query ")] == [
        'Query 1 of 2: {"metric": "obligations", "grain": "fiscal_month"}',
        'Query 2 of 2: {"metric": "obligations", "grain": "fiscal_month"}',
    ]


def test_describe_shows_refused_with_its_code_for_a_refusal() -> None:
    refusal = Refusal(code="outside_coverage", reason="Only fiscal year 2025 is held.")
    answer = Answer(question="Q?", status=REFUSED, refusal=refusal, model="model-a", model_calls=1)
    text = describe(answer)
    assert "Refused (outside_coverage): Only fiscal year 2025 is held.\n" in text
    assert "Answer:" not in text and "Failed:" not in text
    assert "Query " not in text and "Model: model-a; 1 model calls, 0 tool calls" in text


def test_describe_shows_failed_with_the_reason_for_a_failure() -> None:
    answer = Answer(
        question="Q?",
        status=FAILED,
        failure="tool budget exhausted: more than 16 calls",
        queries=(MONTHLY,),
        model="model-a",
        model_calls=4,
        tool_calls=16,
    )
    text = describe(answer)
    assert "Failed: tool budget exhausted: more than 16 calls\n" in text
    assert "Answer:" not in text and "Refused" not in text
    assert "Query 1 of 1:" in text, "the queries that did run are still shown"


def test_describe_shows_the_first_twenty_rows_and_counts_the_rest() -> None:
    answer = Answer(question="Q?", status=ANSWERED, text="Done.", queries=(run_with_rows(25),))
    lines = describe(answer).splitlines()
    shown = [line for line in lines if line.startswith("    [")]
    assert len(shown) == 20
    assert shown[0] == '    [1, "1.50"]' and shown[-1] == '    [20, "20.50"]'
    assert "Rows (25): m, o" in lines
    assert "    ... 5 more rows in the transcript" in lines
    assert '    [21, "21.50"]' not in lines


def test_describe_shows_exactly_twenty_rows_without_a_remainder_line() -> None:
    answer = Answer(question="Q?", status=ANSWERED, text="Done.", queries=(run_with_rows(20),))
    text = describe(answer)
    assert '    [20, "20.50"]' in text and "more rows" not in text


def test_describe_of_an_answer_without_queries_has_no_query_section() -> None:
    answer = Answer(question="Q?", status=ANSWERED, text="Done.", model="model-a", model_calls=1)
    assert describe(answer) == (
        "Question: Q?\nAnswer: Done.\n\nModel: model-a; 1 model calls, 0 tool calls"
    )
