"""The compiler: bound parameters only, catalog names only, and typed refusals."""

import datetime as dt

import pytest

from agentic_bi.semantic.catalog import load_catalog
from agentic_bi.semantic.compiler import CompiledQuery, compile
from agentic_bi.semantic.errors import (
    DimensionNotAllowedError,
    FilterFieldNotDimensionError,
    InvalidFilterValueError,
    MalformedTimeRangeError,
    QueryRefusal,
    UnknownDimensionError,
    UnknownGrainError,
    UnknownMetricError,
    UnknownOperatorError,
)
from agentic_bi.semantic.query import parse_query

METRICS = sorted(load_catalog().metrics)
HOSTILE = "x'); DROP TABLE fct_contract_transaction; --"
FY2025 = {"start": "2024-10-01", "end": "2025-09-30"}


@pytest.mark.parametrize("metric", METRICS)
def test_no_filter_value_appears_in_the_sql_text(metric: str) -> None:
    compiled = compile(
        {
            "metric": metric,
            "dimensions": ["award_type"],
            "filters": [
                {"field": "sub_agency", "op": "eq", "value": HOSTILE},
                {"field": "psc", "op": "in", "value": ["P'1", "P;2"]},
                {"field": "pop_state", "op": "ne", "value": "V'A"},
            ],
            "grain": "fiscal_month",
            "time_range": FY2025,
        }
    )
    assert isinstance(compiled, CompiledQuery)
    assert isinstance(compiled.sql, str) and isinstance(compiled.params, list)
    for value in (HOSTILE, "P'1", "P;2", "V'A", "DROP", "2024-10-01"):
        assert value not in compiled.sql
    # The compiler never writes a literal: no quote and no statement separator at all.
    assert "'" not in compiled.sql and ";" not in compiled.sql and "--" not in compiled.sql
    assert compiled.sql.count("?") == len(compiled.params) == 6
    assert compiled.params == [
        dt.date(2024, 10, 1),
        dt.date(2025, 9, 30),
        HOSTILE,
        "P'1",
        "P;2",
        "V'A",
    ]


def test_output_columns_are_grain_columns_then_dimensions_then_metric() -> None:
    compiled = compile(
        {"metric": "obligations", "dimensions": ["recipient", "psc"], "grain": "fiscal_quarter"}
    )
    assert compiled.columns == ("fiscal_year", "fiscal_quarter", "recipient", "psc", "obligations")


def test_only_the_joins_a_query_needs_are_emitted() -> None:
    plain = compile({"metric": "obligations"}).sql
    assert "JOIN" not in plain and "WHERE" not in plain and "GROUP BY" not in plain
    rate = compile({"metric": "competition_rate", "grain": "fiscal_year"}).sql
    assert rate.count("LEFT JOIN") == 2
    assert '"dim_competition"' in rate and '"dim_date"' in rate and '"dim_recipient"' not in rate


def test_compile_accepts_a_parsed_query_or_its_json() -> None:
    data = {"metric": "award_count", "dimensions": ["naics"], "time_range": FY2025}
    from_json = compile(data)
    from_parsed = compile(parse_query(data))
    assert (from_json.sql, from_json.params) == (from_parsed.sql, from_parsed.params)
    assert from_json.query == from_parsed.query


def _refusal(query: dict) -> QueryRefusal:
    with pytest.raises(QueryRefusal) as caught:
        compile(query)
    return caught.value


def test_unknown_metric_is_refused() -> None:
    error = _refusal({"metric": "total_spend"})
    assert isinstance(error, UnknownMetricError)
    assert (error.code, error.field) == ("unknown_metric", "metric")


def test_unknown_dimension_is_refused() -> None:
    error = _refusal({"metric": "obligations", "dimensions": ["award_type", "vendor_city"]})
    assert isinstance(error, UnknownDimensionError)
    assert (error.code, error.field) == ("unknown_dimension", "dimensions[1]")


def test_a_physical_column_is_not_a_dimension() -> None:
    error = _refusal({"metric": "obligations", "dimensions": ["sub_agency_code"]})
    assert isinstance(error, UnknownDimensionError)


def test_a_grain_used_as_a_dimension_is_refused_with_a_hint() -> None:
    error = _refusal({"metric": "obligations", "dimensions": ["fiscal_month"]})
    assert isinstance(error, UnknownDimensionError)
    assert "grain" in error.message


@pytest.mark.parametrize(
    ("metric", "dimension"),
    [("supplier_concentration_top10", "recipient"), ("competition_rate", "competed")],
)
def test_dimension_not_allowed_for_the_metric_is_refused(metric: str, dimension: str) -> None:
    error = _refusal({"metric": metric, "dimensions": [dimension]})
    assert isinstance(error, DimensionNotAllowedError)
    assert (error.code, error.field) == ("dimension_not_allowed", "dimensions[0]")


def test_filter_on_a_dimension_not_allowed_for_the_metric_is_refused() -> None:
    filters = [{"field": "competition", "op": "eq", "value": "FC"}]
    error = _refusal({"metric": "competition_rate", "filters": filters})
    assert isinstance(error, DimensionNotAllowedError)
    assert error.field == "filters[0].field"


def test_unknown_operator_is_refused() -> None:
    filters = [
        {"field": "award_type", "op": "eq", "value": "A"},
        {"field": "psc", "op": "like", "value": "R%"},
    ]
    error = _refusal({"metric": "obligations", "filters": filters})
    assert isinstance(error, UnknownOperatorError)
    assert (error.code, error.field) == ("unknown_operator", "filters[1].op")


@pytest.mark.parametrize("field", ["obligation_amount", "obligations", "action_date", "1=1"])
def test_filter_on_a_field_that_is_not_a_dimension_is_refused(field: str) -> None:
    error = _refusal(
        {"metric": "obligations", "filters": [{"field": field, "op": "eq", "value": "1"}]}
    )
    assert isinstance(error, FilterFieldNotDimensionError)
    assert (error.code, error.field) == ("filter_field_not_dimension", "filters[0].field")


def test_malformed_time_range_is_refused_by_compile() -> None:
    error = _refusal({"metric": "obligations", "time_range": {"start": "2025-13-01", "end": "x"}})
    assert isinstance(error, MalformedTimeRangeError)
    assert (error.code, error.field) == ("malformed_time_range", "time_range.start")


def test_unknown_grain_is_refused() -> None:
    error = _refusal({"metric": "obligations", "grain": "calendar_month"})
    assert isinstance(error, UnknownGrainError)
    assert (error.code, error.field) == ("unknown_grain", "grain")


@pytest.mark.parametrize(
    ("item", "reason"),
    [
        ({"field": "award_type", "op": "eq", "value": "E"}, "only these values"),
        ({"field": "award_type", "op": "eq", "value": ["A"]}, "one value"),
        ({"field": "award_type", "op": "in", "value": "A"}, "list of values"),
        ({"field": "competed", "op": "eq", "value": "true"}, "boolean"),
        ({"field": "naics", "op": "eq", "value": 541611}, "string"),
        ({"field": "psc", "op": "in", "value": ["R408", False]}, "string"),
    ],
)
def test_invalid_filter_value_is_refused(item: dict, reason: str) -> None:
    error = _refusal({"metric": "obligations", "filters": [item]})
    assert isinstance(error, InvalidFilterValueError)
    assert error.field == "filters[0].value"
    assert reason in error.message
