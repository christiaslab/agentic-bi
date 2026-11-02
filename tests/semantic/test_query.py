"""The structured query: its JSON schema and the validator that names the offending field."""

import datetime as dt
import json

import pytest

from agentic_bi.semantic.errors import MalformedTimeRangeError, QueryValidationError
from agentic_bi.semantic.query import (
    QUERY_JSON_SCHEMA,
    Filter,
    StructuredQuery,
    TimeRange,
    parse_query,
    query_json_schema,
)

FULL = {
    "metric": "obligations",
    "dimensions": ["sub_agency", "award_type"],
    "filters": [
        {"field": "award_type", "op": "in", "value": ["A", "B"]},
        {"field": "competed", "op": "eq", "value": True},
    ],
    "grain": "fiscal_month",
    "time_range": {"start": "2024-10-01", "end": "2025-09-30"},
}


def test_full_query_parses_and_round_trips() -> None:
    query = parse_query(FULL)
    assert query == StructuredQuery(
        metric="obligations",
        dimensions=("sub_agency", "award_type"),
        filters=(Filter("award_type", "in", ("A", "B")), Filter("competed", "eq", True)),
        grain="fiscal_month",
        time_range=TimeRange(dt.date(2024, 10, 1), dt.date(2025, 9, 30)),
    )
    assert query.to_dict() == FULL
    assert parse_query(json.loads(json.dumps(query.to_dict()))) == query


def test_minimal_query_takes_the_schema_defaults() -> None:
    query = parse_query({"metric": "award_count"})
    assert (query.dimensions, query.filters, query.grain, query.time_range) == (
        (),
        (),
        "total",
        None,
    )
    properties = QUERY_JSON_SCHEMA["properties"]
    assert properties["grain"]["default"] == query.grain
    assert properties["dimensions"]["default"] == list(query.dimensions)
    assert properties["time_range"]["default"] is query.time_range


def test_schema_is_json_and_closed_at_every_level() -> None:
    schema = query_json_schema()
    json.dumps(schema)
    assert schema["required"] == ["metric"]
    assert set(schema["properties"]) == {
        "metric",
        "dimensions",
        "filters",
        "grain",
        "time_range",
        "order_by",
        "limit",
    }
    assert set(schema["properties"]) == set(StructuredQuery.__dataclass_fields__)
    filter_item = schema["properties"]["filters"]["items"]
    time_range = schema["properties"]["time_range"]["oneOf"][1]
    order_by = schema["properties"]["order_by"]["oneOf"][1]
    for node in (schema, filter_item, time_range, order_by):
        assert node["additionalProperties"] is False
    assert set(filter_item["properties"]) == {"field", "op", "value"}
    assert time_range["required"] == ["start", "end"]
    assert order_by["required"] == ["field", "direction"]


def test_schema_export_is_a_copy() -> None:
    schema = query_json_schema()
    schema["properties"]["sql"] = {"type": "string"}
    assert "sql" not in QUERY_JSON_SCHEMA["properties"]


def _with(**changes):
    query = json.loads(json.dumps(FULL))
    query.update(changes)
    return query


def _with_filter(**changes):
    item = {"field": "award_type", "op": "eq", "value": "A"}
    item.update(changes)
    return {"metric": "obligations", "filters": [item]}


INVALID = [
    ("not-an-object", ["obligations"], "query"),
    ("raw-sql-field", _with(sql="SELECT 1"), "sql"),
    ("missing-metric", {"dimensions": []}, "metric"),
    ("metric-not-text", _with(metric=7), "metric"),
    ("metric-empty", _with(metric=""), "metric"),
    ("dimensions-not-list", _with(dimensions="sub_agency"), "dimensions"),
    ("dimension-not-text", _with(dimensions=["sub_agency", None]), "dimensions[1]"),
    ("dimension-repeated", _with(dimensions=["psc", "psc"]), "dimensions[1]"),
    ("filters-not-list", _with(filters={"field": "psc"}), "filters"),
    ("filter-not-object", _with(filters=["psc = 1"]), "filters[0]"),
    (
        "filter-missing-op",
        {"metric": "x", "filters": [{"field": "psc", "value": "A"}]},
        "filters[0].op",
    ),
    ("filter-extra-key", _with_filter(sql="1=1"), "filters[0].sql"),
    ("filter-field-not-text", _with_filter(field=3), "filters[0].field"),
    ("filter-value-float", _with_filter(value=1.5), "filters[0].value"),
    ("filter-value-null", _with_filter(value=None), "filters[0].value"),
    ("filter-value-nested", _with_filter(op="in", value=["A", ["B"]]), "filters[0].value[1]"),
    ("filter-value-empty-list", _with_filter(op="in", value=[]), "filters[0].value"),
    ("filter-too-many-values", _with_filter(op="in", value=["A"] * 101), "filters[0].value"),
    ("grain-not-text", _with(grain=["fiscal_month"]), "grain"),
]


@pytest.mark.parametrize(
    ("data", "field"), [pytest.param(data, field, id=case) for case, data, field in INVALID]
)
def test_validation_error_names_the_offending_field(data, field: str) -> None:
    with pytest.raises(QueryValidationError) as caught:
        parse_query(data)
    assert caught.value.field == field
    assert caught.value.code == "invalid_query"
    assert str(caught.value).startswith(f"{field}: ")


MALFORMED_TIME_RANGES = [
    ("not-an-object", "2024-10-01/2025-09-30", "time_range"),
    ("missing-end", {"start": "2024-10-01"}, "time_range.end"),
    ("extra-key", {"start": "2024-10-01", "end": "2025-09-30", "tz": "UTC"}, "time_range.tz"),
    ("not-iso", {"start": "10/01/2024", "end": "2025-09-30"}, "time_range.start"),
    ("compact-iso", {"start": "20241001", "end": "2025-09-30"}, "time_range.start"),
    ("trailing-newline", {"start": "2024-10-01", "end": "2025-09-30\n"}, "time_range.end"),
    ("not-a-day", {"start": "2025-02-29", "end": "2025-09-30"}, "time_range.start"),
    ("timestamp", {"start": "2024-10-01T00:00:00", "end": "2025-09-30"}, "time_range.start"),
    ("not-text", {"start": 20241001, "end": "2025-09-30"}, "time_range.start"),
    ("start-after-end", {"start": "2025-09-30", "end": "2024-10-01"}, "time_range"),
]


@pytest.mark.parametrize(
    ("time_range", "field"),
    [pytest.param(value, field, id=case) for case, value, field in MALFORMED_TIME_RANGES],
)
def test_malformed_time_range_is_refused(time_range, field: str) -> None:
    with pytest.raises(MalformedTimeRangeError) as caught:
        parse_query({"metric": "obligations", "time_range": time_range})
    assert caught.value.field == field
    assert caught.value.code == "malformed_time_range"
