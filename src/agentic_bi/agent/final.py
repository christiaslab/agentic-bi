"""The model's final reply: one JSON object, an answer or a refusal, checked before it is kept.

The system prompt asks for exactly ``{"status": "answered", "answer": ...}`` or
``{"status": "refused", "code": ..., "reason": ...}``. ``parse_final_reply`` reads it (a code
fence or text around the object is tolerated, never kept). ``check_final_reply`` then holds it
to the rules of the typed answer: an answer needs a query that ran, and every figure in it or
in a refusal's reason must appear in the rows (``figures``); a refusal names a refusal code.
A reply that fails raises ``FinalReplyRejected``, whose message goes back to the model once.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .answer import ANSWERED, REFUSAL_CODES, REFUSED, QueryRun
from .figures import build_evidence, ungrounded

MAX_ANSWER_CHARS = 1200
MAX_REASON_CHARS = 600


class FinalReplyRejected(ValueError):
    """The final reply breaks a rule; the message names the rule for the model and the log."""


@dataclass(frozen=True)
class FinalReply:
    status: str
    answer: str | None = None
    code: str | None = None
    reason: str | None = None


def parse_final_reply(text: str) -> FinalReply:
    data = _json_object(text)
    status = data.get("status")
    if status == ANSWERED:
        _keys(data, {"status", "answer"})
        return FinalReply(ANSWERED, answer=_text(data["answer"], "answer", MAX_ANSWER_CHARS))
    if status == REFUSED:
        _keys(data, {"status", "code", "reason"})
        code = data["code"]
        if code not in REFUSAL_CODES:
            raise FinalReplyRejected(
                f"{code!r} is not a refusal code; use one of: {', '.join(REFUSAL_CODES)}"
            )
        return FinalReply(
            REFUSED, code=code, reason=_text(data["reason"], "reason", MAX_REASON_CHARS)
        )
    raise FinalReplyRejected('"status" must be "answered" or "refused"')


def check_final_reply(
    reply: FinalReply,
    *,
    runs: Sequence[QueryRun],
    question: str,
    coverage: Sequence[dt.date] = (),
    top_n: Iterable[int] = (),
) -> None:
    """Raise ``FinalReplyRejected`` unless the reply keeps the rules of the typed answer."""
    if reply.status == ANSWERED and not runs:
        raise FinalReplyRejected(
            "an answer needs at least one run_query result; run the query, or refuse"
        )
    evidence = build_evidence(runs, coverage=coverage, top_n=top_n, question=question)
    text = reply.answer if reply.status == ANSWERED else reply.reason
    missing = ungrounded(text or "", evidence)
    if missing:
        raise FinalReplyRejected(
            "these figures do not appear in the rows of any query you ran: "
            + "; ".join(missing)
            + ". Give figures as the rows have them (rounding allowed, sign kept), or run a "
            "query for each figure you need"
        )


def feedback(problem: FinalReplyRejected) -> str:
    """The user message that tells the model why its final reply was not accepted."""
    return (
        f"[harness check] Your final reply was not accepted: {problem}. Reply again with "
        "exactly one JSON object, an answer or a refusal, as the system prompt describes."
    )


def _json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0].strip()
    for candidate in (stripped, stripped[stripped.find("{") : stripped.rfind("}") + 1]):
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    raise FinalReplyRejected("the final reply must be exactly one JSON object")


def _keys(data: dict[str, Any], expected: set[str]) -> None:
    if set(data) != expected:
        raise FinalReplyRejected(
            f"a {data['status']} reply has exactly the keys {', '.join(sorted(expected))}"
        )


def _text(value: Any, key: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FinalReplyRejected(f'"{key}" must be non-empty text')
    if len(value) > limit:
        raise FinalReplyRejected(f'"{key}" must be at most {limit} characters')
    return value.strip()
