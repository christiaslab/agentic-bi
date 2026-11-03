"""order_by and limit (ADR-0002, amendment of 2026-11-02): top-N questions, limit bound.

Expected rows on the synthetic fixture are added up by hand from the table in conftest.py,
fiscal year 2025 only (T16 is in fiscal year 2024).
"""

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest

from agentic_bi.semantic.compiler import compile
from agentic_bi.semantic.errors import QueryValidationError
from agentic_bi.semantic.query import (
    MAX_LIMIT,
    QUERY_JSON_SCHEMA,
    OrderBy,
    StructuredQuery,
    parse_query,
)

FY2025 = {"start": "2024-10-01", "end": "2025-09-30"}
TOP5_RECIPIENTS = {
    "metric": "obligations",
    "dimensions": ["recipient"],
    "time_range": FY2025,
    "order_by": {"field": "obligations", "direction": "desc"},
    "limit": 5,
}
TOP5_SQL = """SELECT
    "f"."reporting_uei" AS "recipient",
    sum("f"."obligation_amount") AS "obligations"
FROM "fct_contract_transaction" AS "f"
WHERE "f"."action_date" BETWEEN ? AND ?
GROUP BY "f"."reporting_uei"
ORDER BY "obligations" DESC NULLS LAST, "recipient" ASC NULLS LAST
LIMIT ?"""


def _top5(**changes):
    query = json.loads(json.dumps(TOP5_RECIPIENTS))
    query.update(changes)
    return query


# --- the structured query -------------------------------------------------------------------


def test_top5_query_parses_and_round_trips() -> None:
    query = parse_query(TOP5_RECIPIENTS)
    assert query.order_by == OrderBy("obligations", "desc")
    assert query.limit == 5
    assert query.to_dict() == {**TOP5_RECIPIENTS, "filters": [], "grain": "total"}
    assert parse_query(json.loads(json.dumps(query.to_dict()))) == query


def test_a_query_without_ordering_keeps_its_value_from_before_the_amendment() -> None:
    query = parse_query({"metric": "award_count"})
    assert (query.order_by, query.limit) == (None, None)
    assert set(query.to_dict()) == {"metric", "dimensions", "filters", "grain", "time_range"}
    properties = QUERY_JSON_SCHEMA["properties"]
    assert properties["order_by"]["default"] is None and properties["limit"]["default"] is None


def test_null_order_by_and_null_limit_are_the_defaults() -> None:
    query = parse_query({"metric": "obligations", "order_by": None, "limit": None})
    assert (query.order_by, query.limit) == (None, None)


def test_limit_bounds_are_the_same_in_the_schema_and_the_parser() -> None:
    limit = QUERY_JSON_SCHEMA["properties"]["limit"]["oneOf"][1]
    assert (limit["type"], limit["minimum"], limit["maximum"]) == ("integer", 1, MAX_LIMIT)
    assert MAX_LIMIT == 100
    for value in (1, MAX_LIMIT):
        assert parse_query(_top5(limit=value)).limit == value
    direction = QUERY_JSON_SCHEMA["properties"]["order_by"]["oneOf"][1]["properties"]["direction"]
    assert direction["enum"] == ["asc", "desc"]


def test_order_by_a_requested_dimension_ascending() -> None:
    query = parse_query(_top5(order_by={"field": "recipient", "direction": "asc"}))
    assert query.order_by == OrderBy("recipient", "asc")


INVALID = [
    ("order-by-not-an-object", _top5(order_by="obligations desc"), "order_by"),
    (
        "order-by-a-list",
        _top5(order_by=[{"field": "obligations", "direction": "desc"}]),
        "order_by",
    ),
    (
        "order-by-extra-key",
        _top5(order_by={"field": "obligations", "direction": "desc", "nulls": "first"}),
        "order_by.nulls",
    ),
    ("order-by-missing-direction", _top5(order_by={"field": "obligations"}), "order_by.direction"),
    ("order-by-missing-field", _top5(order_by={"direction": "desc"}), "order_by.field"),
    (
        "order-by-field-not-text",
        _top5(order_by={"field": 1, "direction": "desc"}),
        "order_by.field",
    ),
    (
        "order-by-direction-upper-case",
        _top5(order_by={"field": "obligations", "direction": "DESC"}),
        "order_by.direction",
    ),
    (
        "order-by-direction-a-word",
        _top5(order_by={"field": "obligations", "direction": "largest"}),
        "order_by.direction",
    ),
    (
        "order-by-direction-not-text",
        _top5(order_by={"field": "obligations", "direction": ["desc"]}),
        "order_by.direction",
    ),
    # a bad order_by field: anything that is not an output column the rows can be ranked by
    (
        "order-by-a-dimension-not-requested",
        _top5(order_by={"field": "naics", "direction": "desc"}),
        "order_by.field",
    ),
    (
        "order-by-another-metric",
        _top5(order_by={"field": "award_count", "direction": "desc"}),
        "order_by.field",
    ),
    (
        "order-by-a-physical-column",
        _top5(order_by={"field": "obligation_amount", "direction": "desc"}),
        "order_by.field",
    ),
    (
        "order-by-a-grain-column",
        _top5(grain="fiscal_month", order_by={"field": "fiscal_month", "direction": "desc"}),
        "order_by.field",
    ),
    (
        "order-by-sql-text",
        _top5(order_by={"field": "obligations DESC; DROP TABLE x", "direction": "desc"}),
        "order_by.field",
    ),
    # limit out of range or of the wrong type
    ("limit-zero", _top5(limit=0), "limit"),
    ("limit-negative", _top5(limit=-5), "limit"),
    ("limit-above-the-maximum", _top5(limit=101), "limit"),
    ("limit-huge", _top5(limit=10**12), "limit"),
    ("limit-text", _top5(limit="5"), "limit"),
    ("limit-float", _top5(limit=5.0), "limit"),
    ("limit-boolean", _top5(limit=True), "limit"),
    ("limit-list", _top5(limit=[5]), "limit"),
    (
        "limit-without-order-by",
        {"metric": "obligations", "dimensions": ["recipient"], "limit": 5},
        "limit",
    ),
]


@pytest.mark.parametrize(
    ("data", "field"), [pytest.param(data, field, id=case) for case, data, field in INVALID]
)
def test_bad_order_by_or_limit_is_refused_naming_the_field(data, field: str) -> None:
    for refuse in (parse_query, compile):
        with pytest.raises(QueryValidationError) as caught:
            refuse(data)
        assert (caught.value.code, caught.value.field) == ("invalid_query", field)


def test_a_bad_order_by_field_names_the_fields_that_can_be_used() -> None:
    with pytest.raises(QueryValidationError) as caught:
        parse_query(_top5(order_by={"field": "naics", "direction": "desc"}))
    assert "obligations, recipient" in caught.value.message


HAND_BUILT = [
    (
        StructuredQuery("obligations", ("recipient",), order_by=OrderBy("naics", "desc")),
        "order_by.field",
    ),
    (
        StructuredQuery("obligations", ("recipient",), order_by=OrderBy("obligations", "sideways")),
        "order_by.direction",
    ),
    (
        StructuredQuery(
            "obligations", ("recipient",), order_by=OrderBy("obligations", "desc"), limit=0
        ),
        "limit",
    ),
    (
        StructuredQuery(
            "obligations", ("recipient",), order_by=OrderBy("obligations", "desc"), limit=1000
        ),
        "limit",
    ),
    (StructuredQuery("obligations", ("recipient",), limit=5), "limit"),
]


@pytest.mark.parametrize(("query", "field"), HAND_BUILT)
def test_compile_checks_a_query_built_without_the_parser(query, field: str) -> None:
    with pytest.raises(QueryValidationError) as caught:
        compile(query)
    assert caught.value.field == field


# --- the compiled SQL ------------------------------------------------------------------------


def test_top5_recipients_compiles_with_the_limit_as_a_bound_parameter() -> None:
    compiled = compile(TOP5_RECIPIENTS)
    assert compiled.sql == TOP5_SQL
    assert compiled.params == [dt.date(2024, 10, 1), dt.date(2025, 9, 30), 5]
    assert compiled.sql.count("?") == len(compiled.params)
    assert compiled.columns == ("recipient", "obligations")


@pytest.mark.parametrize("limit", [1, 7, 100])
def test_the_limit_value_never_appears_in_the_sql_text(limit: int) -> None:
    compiled = compile(_top5(limit=limit))
    assert compiled.sql == TOP5_SQL
    assert compiled.params[-1] == limit


def test_top_n_share_orders_and_limits_its_outer_query() -> None:
    compiled = compile(
        {
            "metric": "supplier_concentration_top10",
            "dimensions": ["sub_agency"],
            "filters": [{"field": "pop_state", "op": "ne", "value": "VA"}],
            "time_range": FY2025,
            "order_by": {"field": "supplier_concentration_top10", "direction": "desc"},
            "limit": 3,
        }
    )
    tail = (
        'GROUP BY "sub_agency"\n'
        'ORDER BY "supplier_concentration_top10" DESC NULLS LAST, "sub_agency" ASC NULLS LAST\n'
        "LIMIT ?"
    )
    assert compiled.sql.endswith(tail)
    assert compiled.sql.count("LIMIT") == 1 and compiled.sql.count("ORDER BY") == 2
    assert compiled.params == [dt.date(2024, 10, 1), dt.date(2025, 9, 30), "VA", 3]


def test_order_by_a_dimension_keeps_the_default_keys_after_it() -> None:
    compiled = compile(
        {
            "metric": "award_count",
            "dimensions": ["award_type", "naics"],
            "grain": "fiscal_quarter",
            "order_by": {"field": "naics", "direction": "desc"},
        }
    )
    assert compiled.sql.endswith(
        'ORDER BY "naics" DESC NULLS LAST, "fiscal_year" ASC NULLS LAST, '
        '"fiscal_quarter" ASC NULLS LAST, "award_type" ASC NULLS LAST'
    )
    assert "LIMIT" not in compiled.sql and compiled.params == []


def test_order_by_on_a_total_needs_no_tie_breaker() -> None:
    compiled = compile(
        {
            "metric": "obligations",
            "order_by": {"field": "obligations", "direction": "asc"},
            "limit": 1,
        }
    )
    assert compiled.sql.endswith('ORDER BY "obligations" ASC NULLS LAST\nLIMIT ?')
    assert compiled.params == [1]


# --- results on the synthetic fixture ----------------------------------------------------------


def test_top5_recipients_by_obligations(run) -> None:
    assert run(TOP5_RECIPIENTS) == [
        ("SYNP01", Decimal("1800.00")),
        ("SYNR04", Decimal("1200.00")),
        ("SYNR05", Decimal("1100.00")),
        ("SYNR06", Decimal("1000.00")),
        ("SYNR07", Decimal("900.00")),
    ]


def test_bottom_two_recipients_keep_the_net_negative_one_first(run) -> None:
    rows = run(_top5(order_by={"field": "obligations", "direction": "asc"}, limit=2))
    assert rows == [("SYNR03", Decimal("-250.00")), ("SYNR13", Decimal("300.00"))]


def test_ties_are_broken_by_the_default_order(run) -> None:
    rows = run(
        {
            "metric": "award_count",
            "dimensions": ["recipient"],
            "time_range": FY2025,
            "order_by": {"field": "award_count", "direction": "desc"},
            "limit": 3,
        }
    )
    assert rows == [("SYNP01", 2), ("SYNR03", 1), ("SYNR04", 1)]


def test_the_fiscal_months_with_the_highest_and_lowest_obligations(run) -> None:
    month = {"metric": "obligations", "grain": "fiscal_month", "time_range": FY2025, "limit": 1}
    highest = run({**month, "order_by": {"field": "obligations", "direction": "desc"}})
    lowest = run({**month, "order_by": {"field": "obligations", "direction": "asc"}})
    assert highest == [(2025, 1, Decimal("1300.00"))]
    assert lowest == [(2025, 5, Decimal("-450.00"))]


def test_a_ratio_ranks_the_group_of_work_abroad_like_any_other(run) -> None:
    rows = run(
        {
            "metric": "competition_rate",
            "dimensions": ["pop_state"],
            "time_range": FY2025,
            "order_by": {"field": "competition_rate", "direction": "desc"},
            "limit": 2,
        }
    )
    # abroad: 1500 of 1500 competed; TX: (200 - 450 + 800) of (200 - 450 + 800 + 300)
    assert rows == [(None, 1.0), ("TX", pytest.approx(550 / 850))]


def test_a_top_n_share_tie_is_broken_by_the_dimension(run) -> None:
    rows = run(
        {
            "metric": "supplier_concentration_top10",
            "dimensions": ["sub_agency"],
            "time_range": FY2025,
            "order_by": {"field": "supplier_concentration_top10", "direction": "desc"},
            "limit": 2,
        }
    )
    assert rows == [("SA'3;", 1.0), ("SA1", 1.0)]


def test_ordering_reorders_rows_and_never_changes_a_value(run) -> None:
    every = run({"metric": "obligations", "dimensions": ["recipient"], "time_range": FY2025})
    ordered = run(_top5(limit=MAX_LIMIT))
    assert ordered == sorted(every, key=lambda row: (-row[1], row[0]))
    assert len(ordered) == len(every) == 12


def test_the_top_n_example_in_the_catalog_guide_compiles_to_the_sql_shown() -> None:
    guide = (Path(__file__).resolve().parents[2] / "docs" / "catalog.md").read_text("utf-8")
    section = guide.split("### Ordering and top-N", 1)[1].split("\n## ", 1)[0]
    example = json.loads(section.split("```json\n", 1)[1].split("```", 1)[0])
    sql = section.split("```sql\n", 1)[1].split("```", 1)[0].strip()
    compiled = compile(example)
    assert compiled.sql == sql == TOP5_SQL
    assert compiled.params == [dt.date(2024, 10, 1), dt.date(2025, 9, 30), 5]
