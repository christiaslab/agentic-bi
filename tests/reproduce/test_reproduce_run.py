"""The run on hand-made mappings: failures stay per value, and stale entries are caught.

These use the session's built warehouse, but mappings written here, so each failure mode of
the run can be shown without touching evals/golden_queries.yaml.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from agentic_bi.reproduce import (
    DIFFERS,
    ERROR,
    NOT_EXPRESSIBLE,
    REPRODUCED,
    Golden,
    GoldenMapping,
    MappedQuery,
    NotExpressible,
    SelectionError,
    report,
    reproduce,
    select_value,
    summary,
)
from agentic_bi.reproduce.mapping import Attempt
from agentic_bi.semantic import execute, parse_query

FY = {"start": "2024-10-01", "end": "2025-09-30"}


def _mapped(value_id: str, query: dict, row: dict | None = None, known: str | None = None):
    return MappedQuery(id=value_id, query=parse_query(query), row=row or {}, known_difference=known)


def _run(connection, golden: Golden, *entries) -> dict:
    subset = Golden(
        capture_date=golden.capture_date,
        values=tuple(v for v in golden.values if v.id in {e.id for e in entries}),
    )
    mapping = GoldenMapping(golden_capture_date=golden.capture_date, entries=tuple(entries))
    return {o.id: o for o in reproduce(connection, mapping, subset)}


def test_a_refused_query_is_an_error_outcome_and_the_others_still_run(
    query_connection, golden: Golden
) -> None:
    outcomes = _run(
        query_connection,
        golden,
        # competition_rate may not be grouped by competition: a well-formed query the
        # catalog refuses, standing in for a compiler failure
        _mapped("obligations.total", {"metric": "competition_rate", "dimensions": ["competition"]}),
        _mapped("awards.active_in_period.count", {"metric": "award_count", "time_range": FY}),
    )
    failed = outcomes["obligations.total"]
    assert failed.status == ERROR
    assert "dimension_not_allowed" in failed.reason
    assert not failed.expected
    assert outcomes["awards.active_in_period.count"].status == REPRODUCED


def test_a_wrong_mapping_differs_and_keeps_both_numbers(query_connection, golden: Golden) -> None:
    outcomes = _run(
        query_connection,
        golden,
        _mapped(
            "obligations.pop_state.CA",
            {
                "metric": "obligations",
                "filters": [{"field": "pop_state", "op": "eq", "value": "OH"}],
                "time_range": FY,
            },
        ),
    )
    outcome = outcomes["obligations.pop_state.CA"]
    assert outcome.status == DIFFERS and not outcome.expected
    assert outcome.golden == "238749959.14"
    assert outcome.reproduced == Decimal("169208815.00")
    assert outcome.difference == Decimal("169208815.00") - Decimal("238749959.14")


def test_a_known_difference_that_now_matches_is_unexpected(
    query_connection, golden: Golden
) -> None:
    outcomes = _run(
        query_connection,
        golden,
        _mapped("obligations.total", {"metric": "obligations", "time_range": FY}, known="stale"),
    )
    outcome = outcomes["obligations.total"]
    assert outcome.status == REPRODUCED
    assert not outcome.expected
    assert summary(tuple(outcomes.values()))["unexpected"] == ["obligations.total"]


def test_a_not_expressible_value_whose_attempt_now_compiles_is_unexpected(
    query_connection, golden: Golden
) -> None:
    entry = NotExpressible(
        id="transactions.count",
        reason="test",
        disposition="candidate_catalog_change",
        attempt=Attempt(query={"metric": "award_count"}, refusal="unknown_metric"),
    )
    outcome = _run(query_connection, golden, entry)["transactions.count"]
    assert outcome.status == NOT_EXPRESSIBLE
    assert outcome.attempt["refusal"] is None
    assert not outcome.expected


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({}, "grouping columns"),
        ({"competition": "A", "fiscal_month": 1}, "grouping columns"),
        ({"competition": "Z"}, "selects 0 rows"),
        # a code is text: the integer 1 never selects the row of the text "1"
        ({"competition": 1}, "selects 0 rows"),
    ],
)
def test_a_row_selector_must_pick_exactly_one_row(
    query_connection, golden: Golden, row: dict, message: str
) -> None:
    query = {"metric": "obligations", "dimensions": ["competition"], "time_range": FY}
    result = execute(query_connection, query)
    with pytest.raises(SelectionError, match=message):
        select_value(result, row)
    outcome = _run(query_connection, golden, _mapped("obligations.extent_competed.A", query, row))[
        "obligations.extent_competed.A"
    ]
    assert outcome.status == ERROR and message in outcome.reason
    assert outcome.sql is not None


def test_a_null_selector_picks_the_row_with_no_code(query_connection) -> None:
    query = {"metric": "obligations", "dimensions": ["competition"], "time_range": FY}
    result = execute(query_connection, query)
    assert select_value(result, {"competition": None}) == Decimal("-47387.44")


def test_a_one_row_result_takes_an_empty_selector(query_connection) -> None:
    result = execute(query_connection, {"metric": "award_count", "time_range": FY})
    assert select_value(result, {}) == 6016
    with pytest.raises(SelectionError):
        select_value(result, {"award_type": "A"})


def test_report_is_json_with_decimal_strings_and_fixed_point_differences(
    outcomes, golden: Golden, tmp_path: Path
) -> None:
    document = report(
        tuple(outcomes.values()),
        golden=golden,
        golden_path=Path("docs/golden/golden.json"),
        mapping_path=Path("evals/golden_queries.yaml"),
        database=tmp_path / "warehouse.duckdb",
    )
    text = json.dumps(document)
    assert "E-" not in text and "E+" not in text
    values = {value["id"]: value for value in json.loads(text)["values"]}
    assert len(values) == 48
    for value in values.values():
        assert {"golden", "reproduced", "difference", "status", "expected"} <= set(value)
    share = values["recipients.top10_reporting_share"]
    assert share["golden"] == "0.449020"
    assert share["reproduced"] == "0.4490202395623079"
    assert share["difference"] == "0.0000002395623079"
    assert share["tolerance"] == "0.0000005"
    assert values["obligations.total"]["difference"] == "0.00"
    assert values["obligations.total"]["params"] == ["2024-10-01", "2025-09-30"]
    assert values["awards.active_in_period.count"]["reproduced"] == 6016
    assert values["transactions.count"]["reproduced"] is None
    assert values["transactions.count"]["disposition"] == "candidate_catalog_change"
