"""Deterministic checks: what the verifier decides with no model (ADR-0006).

Three checks run before any judge sees a query, in this order:

1. **Schema.** The query must match the structured-query JSON schema (``parse_query``).
2. **Catalog.** Every name must be in the catalog and allowed for the metric (``compile``).
3. **Slice.** The time range must lie inside the slice the warehouse holds (ADR-0001).

A failed check is a verdict on its own: ``inconsistent`` for a schema or catalog failure,
``refuse_expected`` when the only failure is a time range wholly outside the slice (the question
is about data the warehouse does not hold), and ``inconsistent`` when a time range only partly
leaves it. A partial overlap cannot tell an out-of-slice question ("calendar 2025") from a
date error on an answerable one ("fiscal 2025" sent as October 2024 to October 2025), so it does
not claim the question should be refused. A query that passes all three goes to the judge,
which decides intent.

Top-N fields. ``order_by`` and ``limit`` are part of the structured query (the amendment to
ADR-0002): ``parse_query`` checks them like any other field. ``order_by`` names the metric or a
requested dimension, ascending or descending; ``limit`` is 1 to 100 and needs an ``order_by``.
The checks report them as ``top_n`` so the judge and the report can show the cut.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agentic_bi.ingest import spec
from agentic_bi.semantic import Catalog, compile, default_catalog, parse_query
from agentic_bi.semantic.errors import QueryRefusal, QueryValidationError
from agentic_bi.semantic.query import DIRECTIONS, StructuredQuery, TimeRange
from agentic_bi.semantic.query import MAX_LIMIT as MAX_LIMIT

from .verdict import Issue, Verdict, check_verdict_rules

SLICE = TimeRange(dt.date.fromisoformat(spec.START_DATE), dt.date.fromisoformat(spec.END_DATE))

TOP_N_FIELDS = ("order_by", "limit")
ORDER_DIRECTIONS = DIRECTIONS
MIN_LIMIT = 1


@dataclass(frozen=True)
class TopN:
    """The ``order_by`` and ``limit`` of a checked query (see the module docstring)."""

    field: str | None
    direction: str | None
    limit: int | None


@dataclass(frozen=True)
class CheckResult:
    """The outcome of the deterministic checks on one query."""

    issues: tuple[Issue, ...]
    query: StructuredQuery | None
    top_n: TopN | None = None
    # "wholly" or "partly" when the time range leaves the slice, else None.
    outside_slice: str | None = None

    @property
    def passed(self) -> bool:
        return not self.issues

    def verdict(self) -> Verdict | None:
        """The deterministic verdict when a check failed; None when every check passed."""
        if self.passed:
            return None
        codes = tuple(issue.code for issue in self.issues)
        if set(codes) == {"time_range_outside_slice"} and self.outside_slice == "wholly":
            outcome = "refuse_expected"
            rationale = "The question asks about dates the warehouse does not hold."
        elif set(codes) == {"time_range_outside_slice"}:
            outcome = "inconsistent"
            rationale = (
                "The time range reaches beyond the slice, so the answer would be silently "
                "partial; either the dates are wrong or the question should be refused."
            )
        else:
            outcome = "inconsistent"
            rationale = (
                "The query cannot run: it fails the schema or names something outside the catalog."
            )
        check_verdict_rules(outcome, codes)
        return Verdict(
            verdict=outcome, issues=self.issues, rationale=rationale, stage="deterministic"
        )

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "issues": [issue.to_dict() for issue in self.issues]}


def run_checks(
    data: Any, catalog: Catalog | None = None, slice_range: TimeRange = SLICE
) -> CheckResult:
    """Run the three deterministic checks on a decoded JSON value of a structured query."""
    catalog = catalog if catalog is not None else default_catalog()
    if not isinstance(data, Mapping):
        return CheckResult((_invalid("query", "expected an object", "invalid_query"),), None)

    issues: list[Issue] = []
    try:
        query = parse_query(data)
    except QueryValidationError as exc:
        return CheckResult((_invalid(exc.field, exc.message, exc.code),), None)

    # parse_query refuses a limit without an order_by, so order_by alone marks a top-N query.
    top_n: TopN | None = None
    if query.order_by is not None:
        top_n = TopN(query.order_by.field, query.order_by.direction, query.limit)

    try:
        compile(query, catalog)
    except QueryRefusal as exc:
        issues.append(
            Issue(
                code="outside_catalog",
                field=exc.field,
                explanation=exc.message,
                refusal_code=exc.code,
            )
        )

    slice_issue, outside = _slice_issue(query, slice_range)
    if slice_issue is not None:
        issues.append(slice_issue)
    return CheckResult(tuple(issues), query, top_n, outside)


def _invalid(field: str, message: str, refusal_code: str) -> Issue:
    return Issue(code="invalid_query", field=field, explanation=message, refusal_code=refusal_code)


def _slice_issue(query: StructuredQuery, slice_range: TimeRange) -> tuple[Issue | None, str | None]:
    time_range = query.time_range
    if time_range is None:
        return None, None
    if time_range.start >= slice_range.start and time_range.end <= slice_range.end:
        return None, None
    disjoint = time_range.end < slice_range.start or time_range.start > slice_range.end
    extent = "lies wholly outside" if disjoint else "reaches beyond"
    issue = Issue(
        code="time_range_outside_slice",
        field="time_range",
        explanation=(
            f"{time_range.start.isoformat()} to {time_range.end.isoformat()} {extent} the "
            f"slice the warehouse holds, {slice_range.start.isoformat()} to "
            f"{slice_range.end.isoformat()}; an answer would be empty or silently partial"
        ),
    )
    return issue, "wholly" if disjoint else "partly"
