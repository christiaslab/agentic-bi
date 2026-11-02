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

Top-N fields. ADR-0002 is being amended in S4 to add ``order_by`` and ``limit``. Until that
lands, a query carrying them fails ``parse_query`` on exactly that field; the checks then read
them with the provisional rule below and check the rest of the query as usual. Once the
structured query accepts them natively, ``parse_query`` and ``compile`` decide, and the
provisional rule is never reached. The rule follows the S4 brief: ``order_by`` names the
metric or a requested dimension, ascending or descending; ``limit`` is 1 to 100.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agentic_bi.ingest import spec
from agentic_bi.semantic import Catalog, compile, default_catalog, parse_query
from agentic_bi.semantic.errors import QueryRefusal, QueryValidationError
from agentic_bi.semantic.query import StructuredQuery, TimeRange

from .verdict import Issue, Verdict, check_verdict_rules

SLICE = TimeRange(dt.date.fromisoformat(spec.START_DATE), dt.date.fromisoformat(spec.END_DATE))

TOP_N_FIELDS = ("order_by", "limit")
ORDER_DIRECTIONS = ("asc", "desc")
MIN_LIMIT = 1
MAX_LIMIT = 100
_UNKNOWN_FIELD = "is not a field of the structured query"


@dataclass(frozen=True)
class TopN:
    """The provisional reading of ``order_by`` and ``limit`` (see the module docstring)."""

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

    base: Mapping[str, Any] = data
    top_n: TopN | None = None
    issues: list[Issue] = []
    try:
        parse_query(data)
    except QueryValidationError as exc:
        if exc.field in TOP_N_FIELDS and exc.message == _UNKNOWN_FIELD:
            base = {key: value for key, value in data.items() if key not in TOP_N_FIELDS}
            top_n, top_n_issue = _provisional_top_n(data)
            if top_n_issue is not None:
                issues.append(top_n_issue)
        # Any other schema error surfaces again below, from the base query.

    try:
        query = parse_query(base)
    except QueryValidationError as exc:
        return CheckResult((_invalid(exc.field, exc.message, exc.code),), None)

    if top_n is not None and top_n.field is not None:
        allowed = (query.metric, *query.dimensions)
        if top_n.field not in allowed:
            issues.append(
                _invalid(
                    "order_by.field",
                    f"{top_n.field!r} is neither the metric nor a requested dimension",
                    "invalid_query",
                )
            )

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


def _provisional_top_n(data: Mapping[str, Any]) -> tuple[TopN, Issue | None]:
    order_by = data.get("order_by")
    limit = data.get("limit")
    field: str | None = None
    direction: str | None = None
    if "order_by" in data:
        if not isinstance(order_by, Mapping):
            return TopN(None, None, None), _invalid(
                "order_by", "expected an object with field and direction", "invalid_query"
            )
        for key in order_by:
            if key not in ("field", "direction"):
                return TopN(None, None, None), _invalid(
                    f"order_by.{key}", "is not a field of order_by", "invalid_query"
                )
        field = order_by.get("field")
        direction = order_by.get("direction")
        if not isinstance(field, str) or not 1 <= len(field) <= 64:
            return TopN(None, None, None), _invalid(
                "order_by.field", "expected the metric or a requested dimension", "invalid_query"
            )
        if direction not in ORDER_DIRECTIONS:
            return TopN(None, None, None), _invalid(
                "order_by.direction", "expected asc or desc", "invalid_query"
            )
    if "limit" in data:
        if type(limit) is not int or not MIN_LIMIT <= limit <= MAX_LIMIT:
            return TopN(field, direction, None), _invalid(
                "limit", f"expected an integer from {MIN_LIMIT} to {MAX_LIMIT}", "invalid_query"
            )
        if "order_by" not in data:
            return TopN(None, None, limit), _invalid(
                "limit",
                "a limit needs an order_by, or the rows it keeps are arbitrary",
                "invalid_query",
            )
    return TopN(field, direction, limit), None


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
