"""Compile a structured query into parameterised DuckDB SQL over the star-schema contract.

Two rules make the output safe by construction (ADR-0002 points 3 and 4):

- Every identifier in the SQL text comes from the catalog or the contract module, both checked
  as plain lowercase names. A name in the query only selects among them; anything else is a
  typed refusal.
- Every value from the query (filter values and the dates of the time range) becomes a ``?``
  placeholder with the value in the parameter list. The compiler never writes a string
  literal, so the SQL text holds no quote character at all.

Joins to dimensions are LEFT joins: a fact row is never dropped from a total because it has
no dimension row (the contract audits that case). Time ranges filter the fact's
``action_date`` and are inclusive on both ends.

Rows come in the default order (time grain, then dimensions, ascending). An ``order_by``
puts its own key first and keeps the default keys after it as tie-breakers, so a top-N cut
is always the same rows; a ``limit`` is a ``?`` placeholder like every other value.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from . import contract
from .catalog import Catalog, ColumnRef, Dimension, Metric, default_catalog
from .errors import (
    CatalogError,
    DimensionNotAllowedError,
    FilterFieldNotDimensionError,
    InvalidFilterValueError,
    UnknownDimensionError,
    UnknownGrainError,
    UnknownMetricError,
    UnknownOperatorError,
)
from .query import Filter, StructuredQuery, check_ordering, parse_query

_IDENTIFIER = re.compile(r"_?[a-z][a-z0-9_]{0,62}")
_INDENT = "    "


@dataclass(frozen=True)
class CompiledQuery:
    """SQL text with ``?`` placeholders, the values bound to them in order, and output names."""

    sql: str
    params: list[Any]
    columns: tuple[str, ...]
    query: StructuredQuery


# Named after the brief's compile(query); it shadows the builtin only inside this module.
def compile(
    query: StructuredQuery | Mapping[str, Any], catalog: Catalog | None = None
) -> CompiledQuery:
    """Compile a structured query, or a decoded JSON value of one, against the catalog."""
    if not isinstance(query, StructuredQuery):
        query = parse_query(query)
    catalog = catalog if catalog is not None else default_catalog()

    metric = catalog.metrics.get(query.metric)
    if metric is None:
        raise UnknownMetricError(
            "metric",
            f"{query.metric!r} is not a catalog metric; known: {', '.join(catalog.metrics)}",
        )
    grain = catalog.grains.get(query.grain)
    if grain is None:
        raise UnknownGrainError(
            "grain", f"{query.grain!r} is not a catalog grain; known: {', '.join(catalog.grains)}"
        )
    dimensions = [
        _allowed_dimension(name, f"dimensions[{i}]", metric, catalog)
        for i, name in enumerate(query.dimensions)
    ]

    params: list[Any] = []
    where: list[str] = []
    if query.time_range is not None:
        time_column = _column(ColumnRef(contract.FACT_TABLE, contract.TIME_COLUMN))
        where.append(f"{time_column} BETWEEN ? AND ?")
        params += [query.time_range.start, query.time_range.end]
    filter_refs = []
    for i, item in enumerate(query.filters):
        sql, values, ref = _filter(item, f"filters[{i}]", metric, catalog)
        where.append(sql)
        params += values
        filter_refs.append(ref)

    groups = [(_column(ref), ref.column) for ref in grain.columns]
    groups += [(_column(dim.column), dim.name) for dim in dimensions]
    refs = list(grain.columns) + [dim.column for dim in dimensions] + filter_refs
    refs += _metric_refs(metric, catalog)
    source = _from_clause(refs)
    order = _order(query, groups)
    limit = query.limit is not None

    if metric.kind == "top_n_share":
        sql = _top_n_share(metric, catalog, groups, source, where, order, limit)
    else:
        select = [f"{expr} AS {_quote(alias)}" for expr, alias in groups]
        select.append(f"{_aggregate(metric, catalog)} AS {_quote(metric.name)}")
        sql = _assemble(select, source, where, [expr for expr, _ in groups], order, limit)
    if limit:
        # LIMIT ? closes the statement, so its value is the last parameter
        params.append(query.limit)

    columns = tuple(alias for _, alias in groups) + (metric.name,)
    return CompiledQuery(sql=sql, params=params, columns=columns, query=query)


def _order(query: StructuredQuery, groups: list[tuple[str, str]]) -> list[str]:
    """ORDER BY items: the requested key, then the default keys as tie-breakers."""
    check_ordering(query)
    keys = [alias for _, alias in groups]
    items = []
    if query.order_by is not None:
        key = query.order_by.field
        direction = {"asc": "ASC", "desc": "DESC"}[query.order_by.direction]
        items.append(f"{_quote(key)} {direction} NULLS LAST")
        keys = [alias for alias in keys if alias != key]
    return items + [f"{_quote(alias)} ASC NULLS LAST" for alias in keys]


def _allowed_dimension(name: str, where: str, metric: Metric, catalog: Catalog) -> Dimension:
    dimension = catalog.dimensions.get(name)
    if dimension is None:
        hint = "; time breakdowns use grain" if name in catalog.grains else ""
        raise UnknownDimensionError(where, f"{name!r} is not a catalog dimension{hint}")
    _check_allowed(dimension, where, metric)
    return dimension


def _check_allowed(dimension: Dimension, where: str, metric: Metric) -> None:
    if dimension.name not in metric.dimensions:
        raise DimensionNotAllowedError(
            where,
            f"{metric.name} cannot be grouped or filtered by {dimension.name}; "
            f"allowed: {', '.join(metric.dimensions)}",
        )


def _filter(
    item: Filter, where: str, metric: Metric, catalog: Catalog
) -> tuple[str, list[Any], ColumnRef]:
    dimension = catalog.dimensions.get(item.field)
    if dimension is None:
        raise FilterFieldNotDimensionError(
            f"{where}.field",
            f"{item.field!r} is not a catalog dimension; filters apply to dimensions only",
        )
    _check_allowed(dimension, f"{where}.field", metric)
    operator = catalog.operators.get(item.op)
    if operator is None:
        raise UnknownOperatorError(
            f"{where}.op",
            f"{item.op!r} is not a catalog operator; known: {', '.join(catalog.operators)}",
        )

    values = list(item.value) if isinstance(item.value, tuple) else [item.value]
    if operator.arity == "one" and isinstance(item.value, tuple):
        raise InvalidFilterValueError(f"{where}.value", f"{item.op} takes one value, not a list")
    if operator.arity == "many" and not isinstance(item.value, tuple):
        raise InvalidFilterValueError(f"{where}.value", f"{item.op} takes a list of values")
    expected = bool if dimension.type == "boolean" else str
    for value in values:
        if type(value) is not expected:
            raise InvalidFilterValueError(
                f"{where}.value", f"{dimension.name} takes {dimension.type} values"
            )
        if dimension.values is not None and value not in dimension.values:
            allowed = ", ".join(str(v) for v in dimension.values)
            raise InvalidFilterValueError(
                f"{where}.value", f"{dimension.name} takes only these values: {allowed}"
            )

    column = _column(dimension.column)
    marks = ", ".join("?" for _ in values)
    sql = {
        "eq": f"{column} = ?",
        "ne": f"{column} IS DISTINCT FROM ?",
        "in": f"{column} IN ({marks})",
        "not_in": f"({column} IS NULL OR {column} NOT IN ({marks}))",
    }[operator.name]
    return sql, values, dimension.column


def _metric_refs(metric: Metric, catalog: Catalog) -> list[ColumnRef]:
    if metric.kind in ("sum", "count_distinct"):
        return [metric.column]
    if metric.kind == "ratio":
        return [
            catalog.metrics[metric.numerator].column,
            catalog.metrics[metric.denominator].column,
        ]
    refs = [catalog.metrics[metric.measure].column]
    if metric.kind == "top_n_share":
        refs.append(catalog.dimensions[metric.entity].column)
    if metric.kind == "share_where":
        refs.append(catalog.dimensions[metric.condition].column)
    return refs


def _aggregate(metric: Metric, catalog: Catalog) -> str:
    """The aggregate expression of a metric over the selected fact rows."""
    if metric.kind == "sum":
        return f"sum({_column(metric.column)})"
    if metric.kind == "count_distinct":
        return f"count(DISTINCT {_column(metric.column)})"
    if metric.kind == "ratio":
        numerator = _aggregate(catalog.metrics[metric.numerator], catalog)
        denominator = _aggregate(catalog.metrics[metric.denominator], catalog)
        return f"{numerator} / nullif({denominator}, 0)"
    if metric.kind == "share_where":
        measure = _aggregate(catalog.metrics[metric.measure], catalog)
        condition = _column(catalog.dimensions[metric.condition].column)
        # A null condition (no competition code) is not true, so it stays out of the numerator.
        return f"coalesce({measure} FILTER (WHERE {condition} IS TRUE), 0) / nullif({measure}, 0)"
    raise CatalogError(f"metrics.{metric.name}: kind {metric.kind} has no single aggregate")


def _top_n_share(
    metric: Metric,
    catalog: Catalog,
    groups: list[tuple[str, str]],
    source: list[str],
    where: list[str],
    order: list[str],
    limit: bool,
) -> str:
    """Share of the measure held by the top n entities, within each output group.

    Entities are ranked by their own total in the group, largest first, ties broken by the
    entity key; a tie at the cut-off cannot change the share, only which key is listed.
    """
    amount = _column(catalog.metrics[metric.measure].column)
    entity = _column(catalog.dimensions[metric.entity].column)
    keys = [_quote(alias) for _, alias in groups]
    selection = [f"{expr} AS {_quote(alias)}" for expr, alias in groups]
    selection += [f"{entity} AS {_quote('_entity')}", f"{amount} AS {_quote('_amount')}"]
    inner = _assemble(selection, source, where, [], [], False)

    per_entity = (
        f"SELECT {', '.join([*keys, _quote('_entity')])}, "
        f"sum({_quote('_amount')}) AS {_quote('_entity_total')}\n"
        f"{_INDENT}FROM {_quote('_selection')}\n"
        f"{_INDENT}GROUP BY {', '.join([*keys, _quote('_entity')])}"
    )
    partition = f"PARTITION BY {', '.join(keys)} " if keys else ""
    ranked = (
        f"SELECT *, row_number() OVER ({partition}ORDER BY {_quote('_entity_total')} DESC, "
        f"{_quote('_entity')} ASC NULLS LAST) AS {_quote('_entity_rank')}\n"
        f"{_INDENT}FROM {_quote('_per_entity')}"
    )
    total = _quote("_entity_total")
    share = (
        f"coalesce(sum({total}) FILTER (WHERE {_quote('_entity_rank')} <= {int(metric.n)}), 0)"
        f" / nullif(sum({total}), 0)"
    )
    outer = _assemble(
        [*keys, f"{share} AS {_quote(metric.name)}"],
        [_quote("_ranked")],
        [],
        keys,
        order,
        limit,
    )
    indented = inner.replace("\n", f"\n{_INDENT}")
    return (
        f"WITH {_quote('_selection')} AS (\n{_INDENT}{indented}\n),\n"
        f"{_quote('_per_entity')} AS (\n{_INDENT}{per_entity}\n),\n"
        f"{_quote('_ranked')} AS (\n{_INDENT}{ranked}\n)\n"
        f"{outer}"
    )


def _from_clause(refs: list[ColumnRef]) -> list[str]:
    tables = {ref.table for ref in refs}
    lines = [f"{_quote(contract.FACT_TABLE)} AS {_quote(contract.ALIASES[contract.FACT_TABLE])}"]
    for table, (dim_key, fact_column) in contract.JOINS.items():
        if table in tables:
            alias = contract.ALIASES[table]
            fact = _column(ColumnRef(contract.FACT_TABLE, fact_column))
            lines.append(
                f"LEFT JOIN {_quote(table)} AS {_quote(alias)} "
                f"ON {_quote(alias)}.{_quote(dim_key)} = {fact}"
            )
    return lines


def _assemble(
    select: list[str],
    source: list[str],
    where: list[str],
    group_by: list[str],
    order: list[str],
    limit: bool,
) -> str:
    lines = ["SELECT", ",\n".join(f"{_INDENT}{item}" for item in select)]
    lines.append(f"FROM {source[0]}")
    lines += source[1:]
    if where:
        lines.append("WHERE " + f"\n{_INDENT}AND ".join(where))
    if group_by:
        lines.append("GROUP BY " + ", ".join(group_by))
    if order:
        lines.append("ORDER BY " + ", ".join(order))
    if limit:
        lines.append("LIMIT ?")
    return "\n".join(lines)


def _column(ref: ColumnRef) -> str:
    if not contract.has_column(ref.table, ref.column):
        raise CatalogError(f"{ref} is not in the star-schema contract")
    return f"{_quote(contract.ALIASES[ref.table])}.{_quote(ref.column)}"


def _quote(identifier: str) -> str:
    # Identifiers come only from the catalog, the contract or this module; this check keeps
    # it that way even if a caller builds a Catalog by hand.
    if not _IDENTIFIER.fullmatch(identifier):
        raise CatalogError(f"{identifier!r} is not a plain identifier")
    return f'"{identifier}"'
