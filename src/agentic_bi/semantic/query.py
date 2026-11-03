"""The structured query of ADR-0002 point 1 and its JSON schema.

The agent never writes SQL; it emits ``{metric, dimensions, filters, grain, time_range}``,
with an optional ``order_by`` and ``limit`` for top-N questions (the ADR's amendment).
``QUERY_JSON_SCHEMA`` is the shape, exported for tool definitions. ``parse_query`` checks a
decoded JSON value against exactly that shape, with no third-party validator, and raises a
``QueryValidationError`` whose ``field`` names the offending part, for example
``filters[0].value``. Whether the names exist in the catalog is the compiler's question, not
this module's; that ``order_by`` names the metric or a requested dimension, and that ``limit``
comes with an ``order_by``, is a check on the query itself, made here (``check_ordering``).
"""

from __future__ import annotations

import copy
import datetime as dt
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .errors import MalformedTimeRangeError, QueryValidationError

MAX_FILTER_VALUES = 100
MAX_LIMIT = 100
DIRECTIONS = ("asc", "desc")
# ASCII digits only; fullmatch, so a trailing newline is refused too.
_ISO_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

_NAME = {"type": "string", "minLength": 1, "maxLength": 64}
_SCALAR = {"type": ["string", "integer", "boolean"]}
_ISO_DATE_SCHEMA = {"type": "string", "format": "date", "pattern": f"^{_ISO_DATE.pattern}$"}

QUERY_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://github.com/christiaslab/agentic-bi/schemas/structured-query-v1.json",
    "title": "Structured query",
    "description": (
        "A question as a metric of the catalog, optional dimensions to group by, filters on "
        "dimensions, a time grain and an inclusive date range. No SQL."
    ),
    "type": "object",
    "additionalProperties": False,
    "required": ["metric"],
    "properties": {
        "metric": {**_NAME, "description": "A metric name from the catalog."},
        "dimensions": {
            "description": "Catalog dimensions to group by, in output order.",
            "type": "array",
            "items": _NAME,
            "uniqueItems": True,
            "default": [],
        },
        "filters": {
            "description": "Conditions on catalog dimensions, all of which must hold.",
            "type": "array",
            "default": [],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["field", "op", "value"],
                "properties": {
                    "field": {**_NAME, "description": "A catalog dimension."},
                    "op": {**_NAME, "description": "A catalog operator."},
                    "value": {
                        "description": "One value, or a list of values for in and not_in.",
                        "oneOf": [
                            _SCALAR,
                            {
                                "type": "array",
                                "items": _SCALAR,
                                "minItems": 1,
                                "maxItems": MAX_FILTER_VALUES,
                            },
                        ],
                    },
                },
            },
        },
        "grain": {
            **_NAME,
            "description": "A catalog time grain; total means no time breakdown.",
            "default": "total",
        },
        "time_range": {
            "description": "Inclusive range of action dates; null means every date held.",
            "default": None,
            "oneOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["start", "end"],
                    "properties": {"start": _ISO_DATE_SCHEMA, "end": _ISO_DATE_SCHEMA},
                },
            ],
        },
        "order_by": {
            "description": (
                "Order of the rows: the metric (by its name) or one of the requested dimensions, "
                "ascending or descending. Ties, and every row when null, keep the default order: "
                "time grain, then dimensions, ascending."
            ),
            "default": None,
            "oneOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["field", "direction"],
                    "properties": {
                        "field": {
                            **_NAME,
                            "description": "The metric's name or one of the query's dimensions.",
                        },
                        "direction": {"type": "string", "enum": list(DIRECTIONS)},
                    },
                },
            ],
        },
        "limit": {
            "description": (
                f"How many rows to keep, from 1 to {MAX_LIMIT}, counted in the order of order_by, "
                "which it needs; null keeps every row."
            ),
            "default": None,
            "oneOf": [{"type": "null"}, {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT}],
        },
    },
}

Scalar = str | int | bool


@dataclass(frozen=True)
class Filter:
    field: str
    op: str
    value: Scalar | tuple[Scalar, ...]


@dataclass(frozen=True)
class TimeRange:
    """Inclusive on both ends."""

    start: dt.date
    end: dt.date


@dataclass(frozen=True)
class OrderBy:
    """Rows ordered by ``field`` (the metric or a requested dimension), ``asc`` or ``desc``."""

    field: str
    direction: str


@dataclass(frozen=True)
class StructuredQuery:
    metric: str
    dimensions: tuple[str, ...] = ()
    filters: tuple[Filter, ...] = ()
    grain: str = "total"
    time_range: TimeRange | None = field(default=None)
    order_by: OrderBy | None = None
    limit: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """The query as a JSON-ready value that ``parse_query`` reads back unchanged.

        ``order_by`` and ``limit`` appear only when set, so a query without them has the
        same value as before the amendment that added them.
        """
        data: dict[str, Any] = {
            "metric": self.metric,
            "dimensions": list(self.dimensions),
            "filters": [
                {
                    "field": f.field,
                    "op": f.op,
                    "value": list(f.value) if isinstance(f.value, tuple) else f.value,
                }
                for f in self.filters
            ],
            "grain": self.grain,
            "time_range": None
            if self.time_range is None
            else {
                "start": self.time_range.start.isoformat(),
                "end": self.time_range.end.isoformat(),
            },
        }
        if self.order_by is not None:
            data["order_by"] = {"field": self.order_by.field, "direction": self.order_by.direction}
        if self.limit is not None:
            data["limit"] = self.limit
        return data


def query_json_schema() -> dict[str, Any]:
    """A copy of the structured-query JSON schema, safe for the caller to change."""
    return copy.deepcopy(QUERY_JSON_SCHEMA)


def parse_query(data: Any) -> StructuredQuery:
    """Check a decoded JSON value against the schema and return the structured query."""
    if not isinstance(data, Mapping):
        raise QueryValidationError("query", "expected an object")
    for key in data:
        if key not in QUERY_JSON_SCHEMA["properties"]:
            raise QueryValidationError(str(key), "is not a field of the structured query")
    if "metric" not in data:
        raise QueryValidationError("metric", "is required")
    query = StructuredQuery(
        metric=_name(data["metric"], "metric"),
        dimensions=_dimensions(data.get("dimensions", [])),
        filters=_filters(data.get("filters", [])),
        grain=_name(data.get("grain", "total"), "grain"),
        time_range=_time_range(data.get("time_range")),
        order_by=_order_by(data.get("order_by")),
        limit=_limit(data.get("limit")),
    )
    check_ordering(query)
    return query


def check_ordering(query: StructuredQuery) -> None:
    """Refuse an ``order_by`` or ``limit`` the query cannot honour.

    ``order_by`` must name the metric or one of the query's dimensions, the output columns a
    row can be ranked by; time order is the default order. A ``limit`` needs an ``order_by``,
    so the rows it keeps are always the ones named, never the first ones by accident. The
    compiler calls this too, for a ``StructuredQuery`` built without ``parse_query``.
    """
    order_by = query.order_by
    if order_by is not None:
        if order_by.field != query.metric and order_by.field not in query.dimensions:
            choices = ", ".join([query.metric, *query.dimensions])
            raise QueryValidationError(
                "order_by.field",
                f"{order_by.field!r} is neither the metric nor a requested dimension; "
                f"order by one of: {choices}",
            )
        if order_by.direction not in DIRECTIONS:
            raise QueryValidationError("order_by.direction", "expected asc or desc")
    if query.limit is not None:
        _limit(query.limit)
        if order_by is None:
            raise QueryValidationError(
                "limit", "needs order_by, the field the rows are ranked by before they are cut"
            )


def _name(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise QueryValidationError(where, "expected a string")
    if not 1 <= len(value) <= _NAME["maxLength"]:
        raise QueryValidationError(where, f"expected 1 to {_NAME['maxLength']} characters")
    return value


def _list(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise QueryValidationError(where, "expected a list")
    return value


def _dimensions(value: Any) -> tuple[str, ...]:
    names = tuple(
        _name(item, f"dimensions[{i}]") for i, item in enumerate(_list(value, "dimensions"))
    )
    for i, name in enumerate(names):
        if name in names[:i]:
            raise QueryValidationError(f"dimensions[{i}]", "repeats an earlier dimension")
    return names


def _scalar(value: Any, where: str) -> Scalar:
    # JSON has no tuples or floats here; bool is checked by type, never as an int.
    if type(value) in (str, int, bool):
        return value
    raise QueryValidationError(where, "expected a string, an integer or a boolean")


def _filters(value: Any) -> tuple[Filter, ...]:
    filters = []
    for i, item in enumerate(_list(value, "filters")):
        where = f"filters[{i}]"
        if not isinstance(item, Mapping):
            raise QueryValidationError(where, "expected an object")
        for key in item:
            if key not in ("field", "op", "value"):
                raise QueryValidationError(f"{where}.{key}", "is not a field of a filter")
        for key in ("field", "op", "value"):
            if key not in item:
                raise QueryValidationError(f"{where}.{key}", "is required")
        raw = item["value"]
        if isinstance(raw, list):
            if not 1 <= len(raw) <= MAX_FILTER_VALUES:
                raise QueryValidationError(
                    f"{where}.value", f"expected 1 to {MAX_FILTER_VALUES} values"
                )
            parsed: Scalar | tuple[Scalar, ...] = tuple(
                _scalar(v, f"{where}.value[{j}]") for j, v in enumerate(raw)
            )
        else:
            parsed = _scalar(raw, f"{where}.value")
        filters.append(
            Filter(
                field=_name(item["field"], f"{where}.field"),
                op=_name(item["op"], f"{where}.op"),
                value=parsed,
            )
        )
    return tuple(filters)


def _date(value: Any, where: str) -> dt.date:
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        raise MalformedTimeRangeError(where, "expected an ISO date, YYYY-MM-DD")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise MalformedTimeRangeError(where, "is not a calendar date") from exc


def _time_range(value: Any) -> TimeRange | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise MalformedTimeRangeError("time_range", "expected an object with start and end")
    for key in value:
        if key not in ("start", "end"):
            raise MalformedTimeRangeError(f"time_range.{key}", "is not a field of a time range")
    for key in ("start", "end"):
        if key not in value:
            raise MalformedTimeRangeError(f"time_range.{key}", "is required")
    start = _date(value["start"], "time_range.start")
    end = _date(value["end"], "time_range.end")
    if start > end:
        raise MalformedTimeRangeError("time_range", "start is after end")
    return TimeRange(start, end)


def _order_by(value: Any) -> OrderBy | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise QueryValidationError("order_by", "expected an object with field and direction")
    for key in value:
        if key not in ("field", "direction"):
            raise QueryValidationError(f"order_by.{key}", "is not a field of order_by")
    for key in ("field", "direction"):
        if key not in value:
            raise QueryValidationError(f"order_by.{key}", "is required")
    name = _name(value["field"], "order_by.field")
    direction = value["direction"]
    if not isinstance(direction, str) or direction not in DIRECTIONS:
        raise QueryValidationError("order_by.direction", "expected asc or desc")
    return OrderBy(field=name, direction=direction)


def _limit(value: Any) -> int | None:
    if value is None:
        return None
    # bool is an int in Python and DuckDB binds True as 1; it is refused by type
    if type(value) is not int or not 1 <= value <= MAX_LIMIT:
        raise QueryValidationError("limit", f"expected an integer from 1 to {MAX_LIMIT}")
    return value
