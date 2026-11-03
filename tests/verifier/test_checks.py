"""The deterministic checks: schema, catalog and slice, and the top-N fields they report."""

import datetime as dt
from typing import Any

import pytest

from agentic_bi.ingest import spec
from agentic_bi.verifier.checks import SLICE, CheckResult, TopN, run_checks
from agentic_bi.verifier.verdict import DETERMINISTIC_ISSUE_CODES

VALID = {
    "metric": "obligations",
    "dimensions": ["sub_agency"],
    "filters": [{"field": "award_type", "op": "in", "value": ["A", "B"]}],
    "grain": "fiscal_quarter",
    "time_range": {"start": "2024-10-01", "end": "2025-03-31"},
}
TOP_5 = {
    "metric": "obligations",
    "dimensions": ["recipient"],
    "order_by": {"field": "obligations", "direction": "desc"},
    "limit": 5,
}


def only_issue(result: CheckResult) -> Any:
    assert len(result.issues) == 1, result.issues
    return result.issues[0]


def test_slice_is_the_ingest_slice() -> None:
    assert SLICE.start == dt.date(2024, 10, 1) == dt.date.fromisoformat(spec.START_DATE)
    assert SLICE.end == dt.date(2025, 9, 30) == dt.date.fromisoformat(spec.END_DATE)


def test_valid_query_passes() -> None:
    result = run_checks(VALID)
    assert result.passed
    assert result.issues == ()
    assert result.verdict() is None
    assert result.query is not None
    assert result.query.metric == "obligations"
    assert result.top_n is None
    assert result.to_dict() == {"passed": True, "issues": []}


def test_minimal_query_passes() -> None:
    assert run_checks({"metric": "award_count"}).passed


@pytest.mark.parametrize("value", [None, "obligations", ["obligations"], 5])
def test_query_that_is_not_an_object_is_invalid(value: Any) -> None:
    result = run_checks(value)
    issue = only_issue(result)
    assert (issue.code, issue.field, issue.refusal_code) == (
        "invalid_query",
        "query",
        "invalid_query",
    )
    assert result.query is None
    verdict = result.verdict()
    assert verdict is not None
    assert verdict.verdict == "inconsistent"
    assert verdict.stage == "deterministic"
    assert verdict.codes == ("invalid_query",)


@pytest.mark.parametrize(
    ("query", "field", "refusal_code"),
    [
        ({}, "metric", "invalid_query"),
        ({"dimensions": ["recipient"]}, "metric", "invalid_query"),
        ({"metric": "obligations", "sql": "select 1"}, "sql", "invalid_query"),
        ({"metric": 3}, "metric", "invalid_query"),
        ({"metric": "obligations", "dimensions": "recipient"}, "dimensions", "invalid_query"),
        ({"metric": "obligations", "dimensions": [3]}, "dimensions[0]", "invalid_query"),
        (
            {"metric": "obligations", "dimensions": ["recipient", "recipient"]},
            "dimensions[1]",
            "invalid_query",
        ),
        ({"metric": "obligations", "filters": {}}, "filters", "invalid_query"),
        (
            {"metric": "obligations", "filters": [{"field": "award_type"}]},
            "filters[0].op",
            "invalid_query",
        ),
        (
            {"metric": "obligations", "filters": [{"field": "a", "op": "eq", "value": 1.5}]},
            "filters[0].value",
            "invalid_query",
        ),
        (
            {"metric": "obligations", "time_range": {"start": "2024-10-01"}},
            "time_range.end",
            "malformed_time_range",
        ),
        (
            {"metric": "obligations", "time_range": {"start": "Oct 1", "end": "2025-09-30"}},
            "time_range.start",
            "malformed_time_range",
        ),
        (
            {"metric": "obligations", "time_range": {"start": "2025-02-30", "end": "2025-03-01"}},
            "time_range.start",
            "malformed_time_range",
        ),
        (
            {"metric": "obligations", "time_range": {"start": "2025-03-01", "end": "2025-01-01"}},
            "time_range",
            "malformed_time_range",
        ),
        ({"metric": "obligations", "time_range": "FY2025"}, "time_range", "malformed_time_range"),
    ],
)
def test_schema_failure_is_invalid_query_with_field_and_refusal_code(
    query: dict[str, Any], field: str, refusal_code: str
) -> None:
    result = run_checks(query)
    issue = only_issue(result)
    assert issue.code == "invalid_query"
    assert issue.field == field
    assert issue.refusal_code == refusal_code
    assert result.query is None
    verdict = result.verdict()
    assert verdict is not None
    assert verdict.verdict == "inconsistent"


@pytest.mark.parametrize(
    ("query", "field", "refusal_code"),
    [
        ({"metric": "revenue"}, "metric", "unknown_metric"),
        ({"metric": "obligations", "dimensions": ["vendor"]}, "dimensions[0]", "unknown_dimension"),
        (
            {"metric": "supplier_concentration_top10", "dimensions": ["recipient"]},
            "dimensions[0]",
            "dimension_not_allowed",
        ),
        ({"metric": "obligations", "grain": "fiscal_week"}, "grain", "unknown_grain"),
        (
            {
                "metric": "obligations",
                "filters": [{"field": "award_type", "op": "like", "value": "A"}],
            },
            "filters[0].op",
            "unknown_operator",
        ),
        (
            {
                "metric": "obligations",
                "filters": [{"field": "obligations", "op": "eq", "value": 1}],
            },
            "filters[0].field",
            "filter_field_not_dimension",
        ),
        (
            {
                "metric": "obligations",
                "filters": [{"field": "award_type", "op": "eq", "value": "Z"}],
            },
            "filters[0].value",
            "invalid_filter_value",
        ),
    ],
)
def test_catalog_failure_is_outside_catalog_with_the_compiler_code(
    query: dict[str, Any], field: str, refusal_code: str
) -> None:
    result = run_checks(query)
    issue = only_issue(result)
    assert issue.code == "outside_catalog"
    assert issue.field == field
    assert issue.refusal_code == refusal_code
    assert issue.explanation
    assert result.query is not None
    verdict = result.verdict()
    assert verdict is not None
    assert verdict.verdict == "inconsistent"
    assert verdict.stage == "deterministic"
    assert verdict.codes == ("outside_catalog",)


def test_time_range_wholly_before_the_slice_is_refuse_expected() -> None:
    query = {"metric": "obligations", "time_range": {"start": "2023-10-01", "end": "2024-09-30"}}
    result = run_checks(query)
    issue = only_issue(result)
    assert (issue.code, issue.field) == ("time_range_outside_slice", "time_range")
    assert issue.refusal_code is None
    assert "wholly outside" in issue.explanation
    verdict = result.verdict()
    assert verdict is not None
    assert verdict.verdict == "refuse_expected"
    assert verdict.codes == ("time_range_outside_slice",)


def test_time_range_wholly_after_the_slice_is_refuse_expected() -> None:
    query = {"metric": "obligations", "time_range": {"start": "2025-10-01", "end": "2025-12-31"}}
    verdict = run_checks(query).verdict()
    assert verdict is not None
    assert verdict.verdict == "refuse_expected"


@pytest.mark.parametrize(
    ("start", "end"),
    [("2025-01-01", "2025-10-31"), ("2024-09-30", "2025-09-30"), ("2024-10-01", "2025-10-01")],
)
def test_time_range_partly_outside_the_slice_is_inconsistent(start: str, end: str) -> None:
    """A partial overlap may be a date error on an answerable question, so it claims no refusal."""
    query = {"metric": "obligations", "time_range": {"start": start, "end": end}}
    result = run_checks(query)
    issue = only_issue(result)
    assert issue.code == "time_range_outside_slice"
    assert "reaches beyond" in issue.explanation
    verdict = result.verdict()
    assert verdict is not None
    assert verdict.verdict == "inconsistent"
    assert result.outside_slice == "partly"
    assert verdict.stage == "deterministic"


@pytest.mark.parametrize(
    "time_range",
    [
        None,
        {"start": "2024-10-01", "end": "2025-09-30"},
        {"start": "2025-03-01", "end": "2025-03-31"},
        {"start": "2025-09-30", "end": "2025-09-30"},
        {"start": "2024-10-01", "end": "2024-10-01"},
    ],
)
def test_time_range_inside_the_slice_or_null_passes(time_range: dict[str, str] | None) -> None:
    assert run_checks({"metric": "obligations", "time_range": time_range}).passed


def test_query_failing_catalog_and_slice_is_inconsistent_with_both_issues() -> None:
    query = {
        "metric": "revenue",
        "time_range": {"start": "2020-01-01", "end": "2020-12-31"},
    }
    result = run_checks(query)
    assert [issue.code for issue in result.issues] == [
        "outside_catalog",
        "time_range_outside_slice",
    ]
    verdict = result.verdict()
    assert verdict is not None
    assert verdict.verdict == "inconsistent"
    assert verdict.codes == ("outside_catalog", "time_range_outside_slice")


def test_every_deterministic_issue_code_is_a_known_code() -> None:
    queries = [
        None,
        {"metric": "revenue", "time_range": {"start": "2020-01-01", "end": "2020-12-31"}},
    ]
    for query in queries:
        for issue in run_checks(query).issues:
            assert issue.code in DETERMINISTIC_ISSUE_CODES


def test_a_custom_slice_is_honoured() -> None:
    from agentic_bi.semantic.query import TimeRange

    narrow = TimeRange(dt.date(2025, 1, 1), dt.date(2025, 1, 31))
    query = {"metric": "obligations", "time_range": {"start": "2025-01-01", "end": "2025-02-01"}}
    assert not run_checks(query, slice_range=narrow).passed
    assert run_checks(query).passed


# --- Top-N: order_by and limit ---------------------------------------------------------


def test_top_5_recipients_by_obligations_passes_and_returns_a_top_n() -> None:
    result = run_checks(TOP_5)
    assert result.passed
    assert result.top_n == TopN(field="obligations", direction="desc", limit=5)
    assert result.query is not None
    assert result.query.dimensions == ("recipient",)


def test_order_by_alone_passes_and_order_by_a_requested_dimension_passes() -> None:
    by_metric = {"metric": "obligations", "order_by": {"field": "obligations", "direction": "asc"}}
    assert run_checks(by_metric).top_n == TopN("obligations", "asc", None)
    by_dimension = {**TOP_5, "order_by": {"field": "recipient", "direction": "asc"}}
    assert run_checks(by_dimension).passed


@pytest.mark.parametrize("limit", [1, 100])
def test_limit_bounds_are_inclusive(limit: int) -> None:
    assert run_checks({**TOP_5, "limit": limit}).passed


def test_top_n_query_is_still_checked_against_the_catalog_and_slice() -> None:
    query = {
        **TOP_5,
        "metric": "revenue",
        "order_by": {"field": "recipient", "direction": "desc"},
        "time_range": {"start": "2020-01-01", "end": "2020-02-01"},
    }
    result = run_checks(query)
    assert [issue.code for issue in result.issues] == [
        "outside_catalog",
        "time_range_outside_slice",
    ]


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        (
            {"order_by": {"field": "sub_agency", "direction": "desc"}},
            "order_by.field",
        ),
        ({"order_by": {"field": "obligations", "direction": "biggest"}}, "order_by.direction"),
        ({"order_by": {"field": "obligations"}}, "order_by.direction"),
        ({"order_by": {"direction": "desc"}}, "order_by.field"),
        ({"order_by": {"field": 3, "direction": "desc"}}, "order_by.field"),
        (
            {"order_by": {"field": "obligations", "direction": "desc", "nulls": "last"}},
            "order_by.nulls",
        ),
        ({"order_by": "obligations"}, "order_by"),
        ({"order_by": ["obligations", "desc"]}, "order_by"),
        # A null order_by is an absent one, so the limit is left without an order.
        ({"order_by": None}, "limit"),
        ({"limit": 0}, "limit"),
        ({"limit": 101}, "limit"),
        ({"limit": -3}, "limit"),
        ({"limit": True}, "limit"),
        ({"limit": 2.5}, "limit"),
        ({"limit": "5"}, "limit"),
    ],
)
def test_bad_top_n_is_invalid_query(changes: dict[str, Any], field: str) -> None:
    result = run_checks({**TOP_5, **changes})
    issue = only_issue(result)
    assert issue.code == "invalid_query"
    assert issue.field == field
    assert issue.refusal_code == "invalid_query"
    verdict = result.verdict()
    assert verdict is not None
    assert verdict.verdict == "inconsistent"


def test_limit_without_order_by_is_invalid_query() -> None:
    result = run_checks({"metric": "obligations", "dimensions": ["recipient"], "limit": 5})
    issue = only_issue(result)
    assert (issue.code, issue.field) == ("invalid_query", "limit")
    assert "order_by" in issue.explanation


def test_unrelated_unknown_key_next_to_order_by_is_still_invalid_query_on_that_key() -> None:
    result = run_checks({**TOP_5, "offset": 10})
    issue = only_issue(result)
    assert (issue.code, issue.field) == ("invalid_query", "offset")
    assert result.query is None
    assert result.top_n is None


def test_unrelated_unknown_key_before_order_by_is_reported_first() -> None:
    query = {"metric": "obligations", "offset": 10, "order_by": TOP_5["order_by"]}
    issue = only_issue(run_checks(query))
    assert issue.field == "offset"
