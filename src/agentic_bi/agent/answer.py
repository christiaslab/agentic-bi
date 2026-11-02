"""The typed answer of the analyst agent.

An ``Answer`` carries the question, every structured query the agent ran with its SQL, its
parameters and its rows (``QueryRun``), and then either a short text answer whose every figure
appears in those rows, or a ``Refusal`` naming the catalog reason, or, when the run could not
finish, a ``failure``. The queries, SQL, parameters and rows come from the tool layer's own
record of what ran, never from what the model says it ran.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentic_bi.semantic.errors import QueryRefusal

from .envelope import json_safe

ANSWERED = "answered"
REFUSED = "refused"
FAILED = "failed"
STATUSES = (ANSWERED, REFUSED, FAILED)

# A question about data the warehouse does not hold (another year, agency or award type).
OUTSIDE_COVERAGE = "outside_coverage"


def _refusal_codes() -> tuple[str, ...]:
    codes: list[str] = []
    pending = [QueryRefusal]
    while pending:
        cls = pending.pop(0)
        pending += cls.__subclasses__()
        # only the compiler's own classes, whatever else subclasses QueryRefusal later
        if cls is not QueryRefusal and cls.__module__ == QueryRefusal.__module__:
            if cls.code not in codes:
                codes.append(cls.code)
    return tuple(codes)


# The compiler's refusal codes (ADR-0002 point 3), read from its error classes, and one more for
# data outside the slice. A refusal must name one of these.
COMPILER_REFUSAL_CODES = _refusal_codes()
REFUSAL_CODES = (*COMPILER_REFUSAL_CODES, OUTSIDE_COVERAGE)


@dataclass(frozen=True)
class QueryRun:
    """One structured query that ran: the query, the SQL it compiled to, and its result."""

    query: dict[str, Any]
    sql: str
    params: tuple[Any, ...]
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]

    @property
    def metric(self) -> str:
        """The metric's output column, always the last one."""
        return self.columns[-1]

    def records(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, row, strict=True)) for row in self.rows]

    def to_dict(self) -> dict[str, Any]:
        return json_safe(
            {
                "query": self.query,
                "sql": self.sql,
                "params": list(self.params),
                "columns": list(self.columns),
                "rows": [list(row) for row in self.rows],
            }
        )


@dataclass(frozen=True)
class Refusal:
    """Why the question is outside the catalog, in the catalog's own terms."""

    code: str
    reason: str
    # The compiler's refusal of a run_query with the same code, when there was one.
    compiler: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "reason": self.reason, "compiler": self.compiler}


@dataclass(frozen=True)
class Answer:
    question: str
    status: str
    text: str | None = None
    refusal: Refusal | None = None
    failure: str | None = None
    queries: tuple[QueryRun, ...] = ()
    model: str = ""
    model_calls: int = 0
    tool_calls: int = 0

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        shape = (self.text is not None, self.refusal is not None, self.failure is not None)
        expected = {ANSWERED: (True, False, False), REFUSED: (False, True, False)}
        if shape != expected.get(self.status, (False, False, True)):
            raise ValueError(f"an answer with status {self.status} has the wrong fields")

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "status": self.status,
            "text": self.text,
            "refusal": None if self.refusal is None else self.refusal.to_dict(),
            "failure": self.failure,
            "queries": [run.to_dict() for run in self.queries],
            "model": self.model,
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
        }
