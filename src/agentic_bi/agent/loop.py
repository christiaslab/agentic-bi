"""The tool runner: the loop that turns one question into a typed answer (ADR-0005).

It sends the question with the system prompt and the four tool definitions, runs every tool
call the model makes through the ``ToolBox``, sends the results back as data, and stops at the
first final reply that keeps the rules (``final``), or when a limit of ``AgentSettings`` is
reached. The history is append-only: each model turn goes back unchanged, tool results and the
harness's own feedback are added after it. Nothing the model or a tool says changes what the
loop does next, except which tool runs with which input.

Every step is recorded in a transcript (``transcript``), so a live run can be checked after
the fact: each model turn, each tool call with its input and the exact text the model got
back, each rejected final reply, and the answer.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from .answer import ANSWERED, FAILED, REFUSED, Answer, QueryRun, Refusal
from .final import FinalReplyRejected, check_final_reply, feedback, parse_final_reply
from .model import ModelClient, ModelError, ModelTurn
from .prompt import PROMPT_VERSION, prompt_sha256, system_prompt
from .settings import AgentSettings
from .tools import COVERAGE_DATES, ToolBox, ToolOutcome

TRANSCRIPT_VERSION = 1
LIVE = "live: every model turn below came from the model named in the turn"


@dataclass(frozen=True)
class AgentResult:
    answer: Answer
    transcript: dict[str, Any]


def ask(
    question: str,
    *,
    model: ModelClient,
    toolbox: ToolBox,
    settings: AgentSettings | None = None,
) -> AgentResult:
    """Answer one question through the tools, or refuse it, or fail with the reason.

    The transcript's provenance is the model's own ``provenance`` when it has one (a replay
    says it is hand-written), else ``LIVE``.
    """
    settings = settings if settings is not None else AgentSettings()
    provenance = getattr(model, "provenance", LIVE)
    if not isinstance(question, str) or not question.strip():
        raise ValueError("the question is empty")
    question = question.strip()
    run = _Run(question, toolbox, settings, provenance)
    system, tools = system_prompt(), toolbox.definitions()
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]

    while run.model_calls < settings.max_model_calls:
        try:
            turn = model.create(system=system, messages=messages, tools=tools, settings=settings)
        except ModelError as exc:
            if exc.fatal:
                raise
            return run.finish(status=FAILED, failure=f"model call failed: {exc}")
        run.turn(turn)
        messages.append({"role": "assistant", "content": turn.content})

        if turn.stop_reason == "tool_use":
            uses = [block for block in turn.content if block.get("type") == "tool_use"]
            if not uses or any(not isinstance(block.get("id"), str) for block in uses):
                return run.finish(status=FAILED, failure="a tool_use turn without a valid call")
            if run.tool_calls + len(uses) > settings.max_tool_calls:
                return run.finish(
                    status=FAILED,
                    failure=f"tool budget exhausted: more than {settings.max_tool_calls} calls",
                )
            results = [run.call(block).tool_result(block["id"]) for block in uses]
            messages.append({"role": "user", "content": results})
            continue

        if turn.stop_reason in ("end_turn", "stop_sequence"):
            text = "\n".join(b.get("text", "") for b in turn.content if b.get("type") == "text")
            try:
                reply = parse_final_reply(text)
                check_final_reply(
                    reply,
                    runs=run.runs,
                    question=question,
                    coverage=COVERAGE_DATES,
                    top_n=run.top_n(),
                )
            except FinalReplyRejected as problem:
                run.event("final_reply", accepted=False, problem=str(problem))
                if run.rejected_replies + 1 >= settings.max_answer_attempts:
                    return run.finish(status=FAILED, failure=f"final reply rejected: {problem}")
                run.rejected_replies += 1
                note = feedback(problem)
                run.event("feedback", text=note)
                messages.append({"role": "user", "content": note})
                continue
            run.event("final_reply", accepted=True, problem=None)
            if reply.status == ANSWERED:
                return run.finish(status=ANSWERED, text=reply.answer)
            compiler = next((r for r in reversed(run.refusals) if r["code"] == reply.code), None)
            refusal = Refusal(code=reply.code, reason=reply.reason, compiler=compiler)
            return run.finish(status=REFUSED, refusal=refusal)

        return run.finish(status=FAILED, failure=_stopped(turn, settings))

    return run.finish(
        status=FAILED,
        failure=f"model call budget exhausted: {settings.max_model_calls} calls",
    )


def _stopped(turn: ModelTurn, settings: AgentSettings) -> str:
    reason = turn.stop_reason
    if reason == "max_tokens":
        return f"the model's reply was cut at max_tokens ({settings.max_tokens})"
    if reason == "refusal":
        category = (turn.stop_details or {}).get("category")
        return "the model declined to continue (stop_reason refusal" + (
            f", category {category})" if category else ")"
        )
    if reason == "pause_turn":
        return "unexpected pause_turn: the agent has no server tools"
    if reason == "model_context_window_exceeded":
        return "the conversation outgrew the model's context window"
    return f"unexpected stop_reason {reason!r}"


class _Run:
    """The state of one question: counters, the queries that ran, and the transcript."""

    def __init__(
        self, question: str, toolbox: ToolBox, settings: AgentSettings, provenance: str
    ) -> None:
        self.question = question
        self.toolbox = toolbox
        self.settings = settings
        self.model = settings.model
        self.model_calls = 0
        self.tool_calls = 0
        self.rejected_replies = 0
        self.runs: list[QueryRun] = []
        self.refusals: list[dict[str, str]] = []
        self.usage: dict[str, int] = {}
        self.events: list[dict[str, Any]] = [{"event": "question", "text": question}]
        self.transcript: dict[str, Any] = {
            "transcript": TRANSCRIPT_VERSION,
            "provenance": provenance,
            "question": question,
            "started_at": _now(),
            "settings": settings.to_dict(),
            "prompt": {"version": PROMPT_VERSION, "sha256": prompt_sha256()},
            "tools": [definition["name"] for definition in toolbox.definitions()],
        }

    def event(self, kind: str, **fields: Any) -> None:
        self.events.append({"event": kind, "turn": self.model_calls, **fields})

    def turn(self, turn: ModelTurn) -> None:
        self.model_calls += 1
        self.model = turn.model
        for key, value in turn.usage.items():
            if isinstance(value, int) and not isinstance(value, bool):
                self.usage[key] = self.usage.get(key, 0) + value
        self.event("model_turn", **turn.to_dict())

    def call(self, block: dict[str, Any]) -> ToolOutcome:
        self.tool_calls += 1
        outcome = self.toolbox.call(block.get("name"), block.get("input"))
        if outcome.run is not None:
            self.runs.append(outcome.run)
        if outcome.refusal is not None:
            self.refusals.append(outcome.refusal)
        self.event(
            "tool_call",
            id=block["id"],
            name=block.get("name"),
            input=block.get("input"),
            is_error=outcome.is_error,
            result=outcome.content,
        )
        return outcome

    def top_n(self) -> list[int]:
        metrics = self.toolbox.catalog.metrics
        return [
            metrics[run.metric].n
            for run in self.runs
            if run.metric in metrics and metrics[run.metric].kind == "top_n_share"
        ]

    def finish(self, **fields: Any) -> AgentResult:
        answer = Answer(
            question=self.question,
            queries=tuple(self.runs),
            model=self.model,
            model_calls=self.model_calls,
            tool_calls=self.tool_calls,
            **fields,
        )
        if answer.status == FAILED:
            self.event("stopped", reason=answer.failure)
        transcript = {
            **self.transcript,
            "finished_at": _now(),
            "usage": dict(sorted(self.usage.items())),
            "events": self.events,
            "answer": answer.to_dict(),
        }
        return AgentResult(answer=answer, transcript=transcript)


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
