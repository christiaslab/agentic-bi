"""The analyst agent's four tools (ADR-0002 points 5, 7 and 9), as plain Python functions.

- ``catalog``: the metrics, dimensions, grains and operators of the catalog with their
  descriptions, and the data the warehouse covers.
- ``schema``: the allowed values of one dimension, with labels where the contract has them.
- ``run_query``: one structured query, compiled and run on the read-only query connection;
  the rows, the SQL and its parameters. It has no SQL input of any kind.
- ``explain_measure``: one metric's plain-language rule.

``ToolBox`` binds them to a query connection, gives the model their definitions and runs a
tool call: it checks the input, turns every refusal or error into an error result, and wraps
every result in the data delimiter of ``envelope``. The schema tool reads dimension tables with
fixed SQL written here from contract names, run through the runner's own guard (one SELECT, a
row limit, a timeout); no text from the model ever becomes SQL.
"""

from __future__ import annotations

import copy
import datetime as dt
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import duckdb

from agentic_bi.ingest import spec
from agentic_bi.semantic import contract
from agentic_bi.semantic.catalog import Catalog, ColumnRef, Dimension, default_catalog
from agentic_bi.semantic.errors import (
    QueryLimitError,
    QueryRefusal,
    QueryTimeoutError,
    RowLimitExceededError,
    SemanticError,
    UnknownDimensionError,
    UnknownMetricError,
)
from agentic_bi.semantic.query import MAX_LIMIT, query_json_schema
from agentic_bi.semantic.runner import _run as run_guarded
from agentic_bi.semantic.runner import check_query_connection, execute

from .answer import QueryRun
from .envelope import TOOL_NAMES, wrap

# The slice of ADR-0001, as the warehouse holds it.
COVERAGE: dict[str, Any] = {
    "agency": f"{spec.AGENCY_NAME} (awarding agency, toptier code {spec.AGENCY_TOPTIER_CODE})",
    "award_types": list(spec.AWARD_TYPE_CODES),
    "start": spec.START_DATE,
    "end": spec.END_DATE,
    "fiscal_years": [spec.FISCAL_YEAR],
    "note": (
        "Prime contract transactions only (award types A, B, C, D), net of de-obligations. "
        "No grants, loans or IDV obligations, no other agency and no other fiscal year."
    ),
}
COVERAGE_DATES = (dt.date.fromisoformat(spec.START_DATE), dt.date.fromisoformat(spec.END_DATE))

# Dimensions whose codes have a name in the contract: dimension -> (table, key, label column).
LABELS: dict[str, tuple[str, str, str]] = {
    "recipient": ("dim_reporting_recipient", "reporting_uei", "reporting_name"),
    "sub_agency": ("dim_sub_agency", "sub_agency_code", "sub_agency_name"),
    "psc": ("dim_psc", "psc_code", "psc_description"),
    "naics": ("dim_naics", "naics_code", "naics_description"),
    "competition": ("dim_competition", "competition_code", "competition_description"),
}

SCHEMA_DEFAULT_LIMIT = 50
SCHEMA_MAX_LIMIT = 200
MAX_CODES = 100
MAX_TEXT = 64
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,62}")


class ToolInputError(Exception):
    """A tool was called with input that does not have the tool's shape."""

    code = "invalid_tool_input"

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


# --- catalog -----------------------------------------------------------------------------------


def catalog(cat: Catalog | None = None) -> dict[str, Any]:
    """Every metric, dimension, grain and operator with its description, and the coverage."""
    cat = cat if cat is not None else default_catalog()
    return {
        "coverage": dict(COVERAGE),
        "metrics": [
            {
                "name": m.name,
                "unit": m.unit,
                "description": m.description,
                "dimensions": list(m.dimensions),
            }
            for m in cat.metrics.values()
        ],
        "dimensions": [
            {
                "name": d.name,
                "type": d.type,
                "description": d.description,
                "values": None if d.values is None else list(d.values),
                "labels": d.name in LABELS,
            }
            for d in cat.dimensions.values()
        ],
        "grains": [
            {
                "name": g.name,
                "description": g.description,
                "columns": [ref.column for ref in g.columns],
            }
            for g in cat.grains.values()
        ],
        "operators": [
            {"name": o.name, "takes": "one value" if o.arity == "one" else "a list of values"}
            | {"description": o.description}
            for o in cat.operators.values()
        ],
        "ordering": (
            "order_by ranks the rows by the metric or by a requested dimension, asc or desc; "
            f"limit keeps 1 to {MAX_LIMIT} rows and needs order_by. Use both for top-N questions."
        ),
    }


# --- explain_measure ---------------------------------------------------------------------------


def explain_measure(metric: str, cat: Catalog | None = None) -> dict[str, Any]:
    """One metric's plain-language rule, how it is built from other metrics, and its slices."""
    cat = cat if cat is not None else default_catalog()
    found = cat.metrics.get(metric) if isinstance(metric, str) else None
    if found is None:
        raise UnknownMetricError(
            "metric", f"{metric!r} is not a catalog metric; known: {', '.join(cat.metrics)}"
        )
    if found.kind == "sum":
        computed = "The sum over the selected transactions, net: de-obligations count as negative."
    elif found.kind == "count_distinct":
        computed = "A count of distinct awards with at least one transaction in the selection."
    elif found.kind == "ratio":
        computed = f"{found.numerator} divided by {found.denominator} over the same selection."
    elif found.kind == "top_n_share":
        computed = (
            f"The share of {found.measure} held by the {found.n} largest {found.entity} values, "
            f"ranked by their own {found.measure} within each row of the result."
        )
    else:
        computed = (
            f"The share of {found.measure} on transactions where {found.condition} is true, out "
            f"of the {found.measure} of the whole selection."
        )
    parts = [p for p in (found.numerator, found.denominator, found.measure) if p is not None]
    return {
        "metric": found.name,
        "unit": found.unit,
        "rule": found.description,
        "computed_as": computed,
        "parts": [{"metric": p, "rule": cat.metrics[p].description} for p in parts],
        "dimensions": list(found.dimensions),
        "not_allowed": [d for d in cat.dimensions if d not in found.dimensions],
    }


# --- run_query ---------------------------------------------------------------------------------


def run_query(
    connection: duckdb.DuckDBPyConnection,
    query: Any,
    *,
    cat: Catalog | None = None,
    max_rows: int = 200,
    timeout_seconds: float = 30.0,
) -> QueryRun:
    """Compile and run one structured query; raises a ``QueryRefusal`` or ``QueryLimitError``.

    ``query`` is the decoded JSON of a structured query. A string, a list, or an object with a
    ``sql`` field is refused by the parser like any other query outside the schema.
    """
    result = execute(
        connection, query, catalog=cat, row_limit=max_rows, timeout_seconds=timeout_seconds
    )
    compiled = result.compiled
    return QueryRun(
        query=compiled.query.to_dict(),
        sql=compiled.sql,
        params=tuple(compiled.params),
        columns=tuple(result.columns),
        rows=tuple(tuple(row) for row in result.rows),
    )


# --- schema ------------------------------------------------------------------------------------


def schema(
    connection: duckdb.DuckDBPyConnection,
    dimension: str,
    *,
    search: str | None = None,
    codes: Sequence[str] | None = None,
    limit: int = SCHEMA_DEFAULT_LIMIT,
    cat: Catalog | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """The allowed values of one dimension, with labels where the contract names them.

    ``search`` keeps values whose code or label contains the text, ignoring case; ``codes``
    keeps only the listed codes and reports the ones not found. Values come sorted by code,
    at most ``limit`` of them, with the number that matched.
    """
    cat = cat if cat is not None else default_catalog()
    found = cat.dimensions.get(dimension) if isinstance(dimension, str) else None
    if found is None:
        raise UnknownDimensionError(
            "dimension",
            f"{dimension!r} is not a catalog dimension; known: {', '.join(cat.dimensions)}",
        )
    needle = None if search is None else search.lower()
    wanted = None if codes is None else list(dict.fromkeys(codes))

    if found.values is not None or found.type == "boolean":
        values = list(found.values) if found.values is not None else [True, False]
        rows = [(v, None) for v in values if _keeps(v, None, needle, wanted)]
        matching, kept = len(rows), rows[:limit]
    else:
        check_query_connection(connection)
        sql, params = _values_sql(found, needle, wanted)
        _, result = run_guarded(connection, sql, [*params, limit], limit, timeout_seconds)
        matching = result[0][2] if result else 0
        kept = [(value, label) for value, label, _ in result]

    labelled = found.name in LABELS
    payload: dict[str, Any] = {
        "dimension": found.name,
        "type": found.type,
        "description": found.description,
        "closed": found.values is not None or found.type == "boolean",
        "labels": labelled,
        "matching": matching,
        "returned": len(kept),
        "truncated": matching > len(kept),
        "values": [
            {"value": value, "label": label} if labelled else {"value": value}
            for value, label in kept
        ],
    }
    if wanted is not None:
        seen = {value for value, _ in kept}
        payload["not_found"] = [code for code in wanted if code not in seen]
    return payload


def _keeps(value: Any, label: str | None, needle: str | None, wanted: list[str] | None) -> bool:
    if wanted is not None and value not in wanted:
        return False
    if needle is None:
        return True
    return needle in str(value).lower() or (label is not None and needle in label.lower())


def _values_sql(
    dimension: Dimension, needle: str | None, wanted: list[str] | None
) -> tuple[str, list[Any]]:
    """Fixed SQL over contract names; the search text and the codes are bound parameters."""
    if dimension.name in LABELS:
        table, key, label = LABELS[dimension.name]
        source = f"{_q(table)} AS {_q('t')}"
        value, name, where = f"{_q('t')}.{_q(key)}", f"{_q('t')}.{_q(label)}", []
    elif dimension.column.table == contract.FACT_TABLE:
        column = f"{_q('f')}.{_q(dimension.column.column)}"
        source = (
            f"(SELECT DISTINCT {column} AS {_q('value')} "
            f"FROM {_q(contract.FACT_TABLE)} AS {_q('f')} WHERE {column} IS NOT NULL) AS {_q('v')}"
        )
        value, name, where = f"{_q('v')}.{_q('value')}", "NULL", []
    else:  # every catalog dimension is labelled, closed, boolean or on the fact
        raise UnknownDimensionError("dimension", f"{dimension.name} has no list of values")
    params: list[Any] = []
    if needle is not None:
        if name == "NULL":
            where.append(f"contains(lower({value}), ?)")
            params.append(needle)
        else:
            where.append(f"(contains(lower({value}), ?) OR contains(lower({name}), ?))")
            params += [needle, needle]
    if wanted is not None:
        where.append(f"{value} IN ({', '.join('?' for _ in wanted)})")
        params += wanted
    sql = (
        f"SELECT {value} AS {_q('value')}, {name} AS {_q('label')}, "
        f"count(*) OVER () AS {_q('matching')}\nFROM {source}"
        + (f"\nWHERE {' AND '.join(where)}" if where else "")
        + f"\nORDER BY {_q('value')} ASC\nLIMIT ?"
    )
    return sql, params


def _q(identifier: str) -> str:
    if not _IDENTIFIER.fullmatch(identifier):
        raise ValueError(f"{identifier!r} is not a plain identifier")
    return f'"{identifier}"'


def check_labels(cat: Catalog | None = None) -> None:
    """Every labelled dimension joins its label table as the contract says; raises otherwise."""
    cat = cat if cat is not None else default_catalog()
    for name, (table, key, label) in LABELS.items():
        dimension = cat.dimensions[name]
        dim_key, fact_column = contract.JOINS[table]
        if (dim_key, dimension.column) != (key, ColumnRef(contract.FACT_TABLE, fact_column)):
            raise ValueError(f"{name} does not join {table} on {key}")
        if not contract.has_column(table, label):
            raise ValueError(f"{table}.{label} is not in the contract")


# --- the tool box ------------------------------------------------------------------------------

_DIMENSION_INPUT = {"type": "string", "description": "A catalog dimension name."}
# run_query's input schema is the structured query's own, added by tool_definitions().
_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "name": "catalog",
        "description": (
            "The metrics catalog: every metric with its rule, unit and allowed dimensions; the "
            "dimensions, time grains and filter operators; how to order and limit rows; and the "
            "data the warehouse covers. Call it first, before any query, unless its result is "
            "already in this conversation."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "schema",
        "description": (
            "The allowed values of one catalog dimension, with their names where the warehouse "
            "has them. Use search to find a code from a name before filtering on it (for "
            "example a recipient's UEI), and codes to name the codes a query returned."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["dimension"],
            "properties": {
                "dimension": _DIMENSION_INPUT,
                "search": {
                    "type": "string",
                    "description": "Keep values whose code or name contains this text, any case.",
                },
                "codes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": f"Keep only these codes, at most {MAX_CODES}.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": SCHEMA_MAX_LIMIT,
                    "description": f"At most this many values; {SCHEMA_DEFAULT_LIMIT} if absent.",
                },
            },
        },
    },
    {
        "name": "run_query",
        "description": (
            "Run one structured query and get its rows, the SQL the compiler wrote and the "
            "parameters bound to it. The input is the structured query itself: one catalog "
            "metric, and optional dimensions, filters, grain, time_range, order_by and limit. "
            "There is no SQL input. A query outside the catalog is refused with a code and the "
            "field it concerns; a result with too many rows is refused too, so narrow it with "
            "filters or rank it with order_by and limit."
        ),
    },
    {
        "name": "explain_measure",
        "description": (
            "The plain-language rule of one catalog metric: what it counts, its sign and unit, "
            "how it is built from other metrics, and which dimensions it allows. Use it when "
            "the question's wording could match a metric's rule in more than one way."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["metric"],
            "properties": {"metric": {"type": "string", "description": "A catalog metric."}},
        },
    },
)


def tool_definitions() -> list[dict[str, Any]]:
    """The four tools as the Messages API takes them; ``run_query`` takes the query schema."""
    query_schema = query_json_schema()
    for key in ("$schema", "$id", "title"):
        query_schema.pop(key, None)
    definitions = copy.deepcopy(list(_DEFINITIONS))
    for definition in definitions:
        if definition["name"] == "run_query":
            definition["input_schema"] = query_schema
    return definitions


@dataclass(frozen=True)
class ToolOutcome:
    """What one tool call did: the text the model gets back and what the loop records."""

    name: str
    input: Any
    content: str
    is_error: bool
    payload: Any
    run: QueryRun | None = None
    refusal: dict[str, str] | None = None

    def tool_result(self, tool_use_id: str) -> dict[str, Any]:
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": self.content,
        }
        if self.is_error:
            block["is_error"] = True
        return block


class ToolBox:
    """The four tools bound to one query connection and one catalog."""

    def __init__(
        self,
        connection: duckdb.DuckDBPyConnection,
        cat: Catalog | None = None,
        *,
        max_rows: int = 200,
        timeout_seconds: float = 30.0,
    ) -> None:
        check_query_connection(connection)
        self.connection = connection
        self.catalog = cat if cat is not None else default_catalog()
        self.max_rows = max_rows
        self.timeout_seconds = timeout_seconds

    def definitions(self) -> list[dict[str, Any]]:
        return tool_definitions()

    def call(self, name: Any, arguments: Any) -> ToolOutcome:
        """Run one tool call from the model. Never raises for anything the model sent."""
        tool = name if isinstance(name, str) and name in TOOL_NAMES else "unknown"
        try:
            if tool == "unknown":
                raise ToolInputError("name", f"there is no such tool; use {', '.join(TOOL_NAMES)}")
            run = None
            if tool == "run_query":
                run = run_query(
                    self.connection,
                    arguments,
                    cat=self.catalog,
                    max_rows=self.max_rows,
                    timeout_seconds=self.timeout_seconds,
                )
                payload = run.to_dict()
                payload["row_count"] = len(run.rows)
            else:
                payload = self._other(tool, _object(arguments))
        except ToolInputError as exc:
            error = {"code": exc.code, "field": exc.field, "message": exc.message}
            return self._error(tool, arguments, error)
        except QueryRefusal as refusal:
            error = {"code": refusal.code, "field": refusal.field, "message": refusal.message}
            return self._error(tool, arguments, error, refusal=tool == "run_query")
        except RowLimitExceededError as exc:
            error = {"code": "row_limit_exceeded", "message": str(exc)}
            return self._error(tool, arguments, error)
        except QueryTimeoutError as exc:
            return self._error(tool, arguments, {"code": "query_timeout", "message": str(exc)})
        except QueryLimitError as exc:  # pragma: no cover - every limit error is named above
            return self._error(tool, arguments, {"code": "query_limit", "message": str(exc)})
        except (SemanticError, duckdb.Error) as exc:
            # not reachable from a valid query; kept as an error result so the run goes on
            # and the transcript shows it
            return self._error(tool, arguments, {"code": "query_error", "message": str(exc)})
        return ToolOutcome(tool, arguments, wrap(tool, payload), False, payload, run=run)

    def _other(self, tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if tool == "catalog":
            _only(arguments, set())
            return catalog(self.catalog)
        if tool == "explain_measure":
            _only(arguments, {"metric"}, required={"metric"})
            return explain_measure(_text(arguments["metric"], "metric"), self.catalog)
        _only(arguments, {"dimension", "search", "codes", "limit"}, required={"dimension"})
        codes = arguments.get("codes")
        if codes is not None:
            if not isinstance(codes, list) or not 1 <= len(codes) <= MAX_CODES:
                raise ToolInputError("codes", f"expected a list of 1 to {MAX_CODES} codes")
            codes = [_text(code, f"codes[{i}]") for i, code in enumerate(codes)]
        search = arguments.get("search")
        limit = arguments.get("limit", SCHEMA_DEFAULT_LIMIT)
        if type(limit) is not int or not 1 <= limit <= SCHEMA_MAX_LIMIT:
            raise ToolInputError("limit", f"expected an integer from 1 to {SCHEMA_MAX_LIMIT}")
        return schema(
            self.connection,
            _text(arguments["dimension"], "dimension"),
            search=None if search is None else _text(search, "search"),
            codes=codes,
            limit=limit,
            cat=self.catalog,
            timeout_seconds=self.timeout_seconds,
        )

    def _error(
        self, tool: str, arguments: Any, error: dict[str, str], *, refusal: bool = False
    ) -> ToolOutcome:
        payload = {"error": error}
        return ToolOutcome(
            tool,
            arguments,
            wrap(tool, payload, error=True),
            True,
            payload,
            refusal=error if refusal else None,
        )


def _object(arguments: Any) -> Mapping[str, Any]:
    if not isinstance(arguments, Mapping):
        raise ToolInputError("input", "expected an object")
    return arguments


def _only(arguments: Mapping[str, Any], allowed: set[str], required: set[str] = frozenset()):
    for key in arguments:
        if key not in allowed:
            raise ToolInputError(str(key), "is not an input of this tool")
    for key in sorted(required):
        if key not in arguments:
            raise ToolInputError(key, "is required")


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_TEXT:
        raise ToolInputError(where, f"expected text of 1 to {MAX_TEXT} characters")
    return value


__all__ = [
    "COVERAGE",
    "LABELS",
    "ToolBox",
    "ToolInputError",
    "ToolOutcome",
    "catalog",
    "check_labels",
    "explain_measure",
    "run_query",
    "schema",
    "tool_definitions",
]
