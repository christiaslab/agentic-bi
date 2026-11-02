"""Run every mapped structured query on the warehouse and compare it with its golden value.

Each query goes through the S3a path an agent's query takes: ``execute`` compiles it from
the catalog and runs the compiler's SQL on the read-only query connection (ADR-0002 points
5 and 6). Nothing here writes SQL. A grouped result is narrowed to the one row the mapping
names; the metric column of that row is the reproduced value.

A failure of one value (a refusal, a database error, no matching row) becomes that value's
``error`` outcome, so one compiler defect never hides the other results.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from agentic_bi.semantic import Catalog, QueryResult, SemanticError, default_catalog, execute
from agentic_bi.semantic import compile as compile_query
from agentic_bi.semantic.errors import QueryRefusal

from .compare import ComparisonError, compare
from .mapping import ROOT, Golden, GoldenMapping, MappedQuery, NotExpressible

REPORT_SCHEMA_VERSION = 1

REPRODUCED = "reproduced"
DIFFERS = "differs"
ERROR = "error"
NOT_EXPRESSIBLE = "not_expressible"


class SelectionError(ValueError):
    """The mapping's row selector does not pick exactly one row of the result."""


@dataclass(frozen=True)
class Outcome:
    """What happened to one golden value."""

    id: str
    tag: str
    unit: str
    status: str
    golden: str | int
    reproduced: Decimal | int | None = None
    difference: Decimal | int | None = None
    rule: str | None = None
    tolerance: Decimal | None = None
    query: Mapping[str, Any] | None = None
    row: Mapping[str, Any] | None = None
    sql: str | None = None
    params: tuple[Any, ...] = ()
    # not-expressible reason, or the error message
    reason: str | None = None
    disposition: str | None = None
    known_difference: str | None = None
    attempt: Mapping[str, Any] | None = field(default=None)

    @property
    def expected(self) -> bool:
        """Whether this outcome is what the mapping says it should be.

        A mapped value must reproduce, unless it carries a known difference, in which case
        it must still differ (a known difference that now matches is stale). A
        not-expressible value must still be refused with the code its attempt names.
        """
        if self.status == NOT_EXPRESSIBLE:
            return self.attempt is None or self.attempt["refusal"] == self.attempt["expected"]
        if self.known_difference is not None:
            return self.status == DIFFERS
        return self.status == REPRODUCED


def reproduce(
    connection: duckdb.DuckDBPyConnection,
    mapping: GoldenMapping,
    golden: Golden,
    catalog: Catalog | None = None,
) -> tuple[Outcome, ...]:
    """Reproduce every golden value of the mapping, in golden order."""
    catalog = catalog if catalog is not None else default_catalog()
    values = golden.by_id()
    results: dict[str, QueryResult | Exception] = {}
    outcomes = []
    for entry in mapping.entries:
        value = values[entry.id]
        if isinstance(entry, NotExpressible):
            outcomes.append(_not_expressible(entry, value, catalog))
        else:
            outcomes.append(_mapped(connection, entry, value, catalog, results))
    return tuple(outcomes)


def select_value(result: QueryResult, row: Mapping[str, Any]) -> Any:
    """The metric value of the one result row that ``row`` selects.

    ``row`` must name every grouping column of the result (grain and dimensions), so it
    can select at most one row by construction; a one-row result takes an empty ``row``.
    """
    metric = result.compiled.columns[-1]
    groups = result.compiled.columns[:-1]
    if set(row) != set(groups):
        raise SelectionError(
            f"row must name exactly the grouping columns {list(groups)}, not {sorted(row)}"
        )
    matches = [
        record
        for record in result.records()
        if all(_same(record[column], selector) for column, selector in row.items())
    ]
    if len(matches) != 1:
        raise SelectionError(f"row {dict(row)} selects {len(matches)} rows, not one")
    return matches[0][metric]


def _same(value: Any, selector: Any) -> bool:
    if selector is None:
        return value is None
    return type(value) is type(selector) and value == selector


def _mapped(
    connection: duckdb.DuckDBPyConnection,
    entry: MappedQuery,
    value,
    catalog: Catalog,
    results: dict[str, QueryResult | Exception],
) -> Outcome:
    query = entry.query.to_dict()
    base = dict(
        id=entry.id,
        tag=value.tag,
        unit=value.unit,
        golden=value.value,
        query=query,
        row=dict(entry.row),
        known_difference=entry.known_difference,
    )
    # several golden values share one grouped query (the fiscal months, the competition
    # codes); it runs once
    key = json.dumps(query, sort_keys=True)
    if key not in results:
        try:
            results[key] = execute(connection, entry.query, catalog=catalog)
        except (SemanticError, duckdb.Error) as exc:
            results[key] = exc
    result = results[key]
    if isinstance(result, Exception):
        return Outcome(**base, status=ERROR, reason=_describe(result))
    traced = dict(sql=result.compiled.sql, params=tuple(result.compiled.params))
    try:
        reproduced = select_value(result, entry.row)
        comparison = compare(catalog.metrics[entry.query.metric].kind, value.value, reproduced)
    except (SelectionError, ComparisonError) as exc:
        return Outcome(**base, **traced, status=ERROR, reason=_describe(exc))
    return Outcome(
        **base,
        **traced,
        status=REPRODUCED if comparison.matches else DIFFERS,
        reproduced=comparison.reproduced,
        difference=comparison.difference,
        rule=comparison.rule,
        tolerance=comparison.tolerance,
    )


def _not_expressible(entry: NotExpressible, value, catalog: Catalog) -> Outcome:
    attempt = None
    if entry.attempt is not None:
        # the nearest query must still be refused, with the code the mapping names; once the
        # catalog can express the value, this outcome turns unexpected and the entry is due
        try:
            compile_query(entry.attempt.query, catalog)
            refusal = None
        except QueryRefusal as exc:
            refusal = exc.code
        attempt = {
            "query": dict(entry.attempt.query),
            "expected": entry.attempt.refusal,
            "refusal": refusal,
        }
    return Outcome(
        id=entry.id,
        tag=value.tag,
        unit=value.unit,
        status=NOT_EXPRESSIBLE,
        golden=value.value,
        reason=entry.reason,
        disposition=entry.disposition,
        attempt=attempt,
    )


def _describe(exc: Exception) -> str:
    code = getattr(exc, "code", None)
    name = type(exc).__name__
    return f"{name} ({code}): {exc}" if code else f"{name}: {exc}"


def summary(outcomes: tuple[Outcome, ...]) -> dict[str, Any]:
    """Counts by status and tag, and the ids whose outcome is not the expected one."""
    reproduced = [o for o in outcomes if o.status == REPRODUCED]
    return {
        "values": len(outcomes),
        "mapped": sum(o.status != NOT_EXPRESSIBLE for o in outcomes),
        "reproduced": len(reproduced),
        "reproduced_owner_api": sum(o.tag == "owner_api" for o in reproduced),
        "reproduced_derived": sum(o.tag == "derived" for o in reproduced),
        "differs": sum(o.status == DIFFERS for o in outcomes),
        "known_differences": sum(o.known_difference is not None for o in outcomes),
        "errors": sum(o.status == ERROR for o in outcomes),
        "not_expressible": sum(o.status == NOT_EXPRESSIBLE for o in outcomes),
        "unexpected": [o.id for o in outcomes if not o.expected],
    }


def report(
    outcomes: tuple[Outcome, ...],
    *,
    golden: Golden,
    golden_path: Path,
    mapping_path: Path,
    database: Path,
) -> dict[str, Any]:
    """The reproduction as a JSON-ready document."""
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "golden_capture_date": golden.capture_date,
        "golden_file": _display(golden_path),
        "mapping_file": _display(mapping_path),
        "database": _display(database),
        "format": (
            "Amounts and ratios are decimal strings, counts integers. difference is reproduced "
            "minus golden. Amounts and counts must be equal (rule exact); ratios must lie "
            "within tolerance, half a unit of the golden value's last decimal place (rule "
            "half_unit_of_last_place). docs/reproduction.md explains both."
        ),
        "summary": summary(outcomes),
        "values": [_outcome_json(o) for o in outcomes],
    }


def write_report(document: Mapping[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _outcome_json(outcome: Outcome) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": outcome.id,
        "tag": outcome.tag,
        "unit": outcome.unit,
        "status": outcome.status,
        "expected": outcome.expected,
        "golden": outcome.golden,
        "reproduced": _number(outcome.reproduced),
        "difference": _number(outcome.difference),
    }
    if outcome.status == NOT_EXPRESSIBLE:
        data["reason"] = outcome.reason
        data["disposition"] = outcome.disposition
        if outcome.attempt is not None:
            data["attempt"] = dict(outcome.attempt)
        return data
    data["rule"] = outcome.rule
    data["tolerance"] = _number(outcome.tolerance)
    if outcome.known_difference is not None:
        data["known_difference"] = outcome.known_difference
    if outcome.status == ERROR:
        data["error"] = outcome.reason
    data["query"] = outcome.query
    data["row"] = outcome.row
    data["sql"] = outcome.sql
    data["params"] = [_param(p) for p in outcome.params]
    return data


def _number(value: Decimal | int | None) -> str | int | None:
    if isinstance(value, Decimal):
        # fixed-point, so a tiny difference reads 0.0000002..., not 2.4E-7
        return format(value, "f")
    return value


def _param(value: Any) -> Any:
    if isinstance(value, dt.date):
        return value.isoformat()
    return value


def _display(path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)
