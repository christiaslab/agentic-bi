"""Load and check the metrics catalog (``catalog/metrics.yaml``).

The loader is strict: an unknown key, a column outside the star-schema contract, a dangling
reference or a name that is not a plain lowercase identifier is a ``CatalogError``. What it
returns is immutable, so the compiler can trust every name it finds here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from . import contract
from .errors import CatalogError

DEFAULT_CATALOG_PATH = Path(__file__).resolve().parents[3] / "catalog" / "metrics.yaml"

CATALOG_VERSION = 1
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

# Operators the compiler can render, with the number of values each takes.
OPERATOR_ARITY: Mapping[str, str] = MappingProxyType(
    {"eq": "one", "ne": "one", "in": "many", "not_in": "many"}
)
DIMENSION_TYPES = ("string", "boolean")
UNITS = ("usd", "awards", "share")
TOTAL_GRAIN = "total"
MAX_TOP_N = 1000

_COMMON_METRIC_KEYS = {"description", "kind", "unit", "dimensions"}
_KIND_KEYS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "sum": frozenset({"column", "sign"}),
        "count_distinct": frozenset({"column", "counts"}),
        "ratio": frozenset({"numerator", "denominator"}),
        "top_n_share": frozenset({"measure", "entity", "n"}),
        "share_where": frozenset({"measure", "condition"}),
    }
)
_SIGNS = ("net",)
_COUNTS = ("active_in_period",)


@dataclass(frozen=True)
class ColumnRef:
    """A contract column, written ``table.column`` in the catalog."""

    table: str
    column: str

    def __str__(self) -> str:
        return f"{self.table}.{self.column}"


@dataclass(frozen=True)
class Grain:
    name: str
    description: str
    columns: tuple[ColumnRef, ...]


@dataclass(frozen=True)
class Dimension:
    name: str
    description: str
    column: ColumnRef
    type: str
    values: tuple[str | bool, ...] | None


@dataclass(frozen=True)
class Operator:
    name: str
    description: str
    arity: str


@dataclass(frozen=True)
class Metric:
    """One catalog metric. Fields beyond the common ones depend on ``kind``."""

    name: str
    description: str
    kind: str
    unit: str
    dimensions: tuple[str, ...]
    column: ColumnRef | None = None
    sign: str | None = None
    counts: str | None = None
    numerator: str | None = None
    denominator: str | None = None
    measure: str | None = None
    entity: str | None = None
    n: int | None = None
    condition: str | None = None


@dataclass(frozen=True)
class Catalog:
    version: int
    grains: Mapping[str, Grain]
    dimensions: Mapping[str, Dimension]
    operators: Mapping[str, Operator]
    metrics: Mapping[str, Metric]


def load_catalog(path: str | Path | None = None) -> Catalog:
    """Read and check a catalog file; the project's own catalog when ``path`` is None."""
    source = Path(path) if path is not None else DEFAULT_CATALOG_PATH
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise CatalogError(f"cannot read the catalog at {source}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CatalogError(f"the catalog at {source} is not valid YAML: {exc}") from exc
    return parse_catalog(data)


@lru_cache(maxsize=1)
def default_catalog() -> Catalog:
    """The project's catalog, loaded once per process."""
    return load_catalog()


def parse_catalog(data: Any) -> Catalog:
    """Check catalog data already parsed from YAML and build the immutable catalog."""
    top = _mapping(data, "catalog")
    _keys(top, "catalog", required={"version", "grains", "dimensions", "operators", "metrics"})
    if top["version"] != CATALOG_VERSION or isinstance(top["version"], bool):
        raise CatalogError(f"catalog: version must be {CATALOG_VERSION}")

    grains = {name: _grain(name, body) for name, body in _named(top["grains"], "grains")}
    dimensions = {
        name: _dimension(name, body) for name, body in _named(top["dimensions"], "dimensions")
    }
    operators = {
        name: _operator(name, body) for name, body in _named(top["operators"], "operators")
    }
    metrics = {name: _metric(name, body) for name, body in _named(top["metrics"], "metrics")}

    if TOTAL_GRAIN not in grains or grains[TOTAL_GRAIN].columns:
        raise CatalogError(f"grains: a '{TOTAL_GRAIN}' grain with no columns is required")
    _check_names_disjoint(grains, dimensions, metrics)
    for metric in metrics.values():
        _check_metric_references(metric, metrics, dimensions)

    return Catalog(
        version=CATALOG_VERSION,
        grains=MappingProxyType(grains),
        dimensions=MappingProxyType(dimensions),
        operators=MappingProxyType(operators),
        metrics=MappingProxyType(metrics),
    )


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CatalogError(f"{where}: expected a mapping")
    return value


def _keys(
    body: Mapping[str, Any],
    where: str,
    required: set[str] | frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> None:
    missing = sorted(required - body.keys())
    if missing:
        raise CatalogError(f"{where}: missing {', '.join(missing)}")
    unknown = sorted(set(body) - required - optional, key=str)
    if unknown:
        raise CatalogError(f"{where}: unknown key {', '.join(map(str, unknown))}")


def _named(value: Any, where: str) -> list[tuple[str, Any]]:
    section = _mapping(value, where)
    if not section:
        raise CatalogError(f"{where}: must not be empty")
    for name in section:
        if not isinstance(name, str) or not NAME_PATTERN.match(name):
            raise CatalogError(f"{where}: {name!r} is not a lowercase identifier")
    return list(section.items())


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{where}: expected non-empty text")
    return value.strip()


def _column(value: Any, where: str) -> ColumnRef:
    if not isinstance(value, str) or value.count(".") != 1:
        raise CatalogError(f"{where}: expected table.column")
    table, column = value.split(".")
    if not contract.has_column(table, column):
        raise CatalogError(f"{where}: {value} is not in the star-schema contract")
    return ColumnRef(table, column)


def _choice(value: Any, choices: tuple[str, ...], where: str) -> str:
    if value not in choices:
        raise CatalogError(f"{where}: expected one of {', '.join(choices)}")
    return value


def _grain(name: str, body: Any) -> Grain:
    where = f"grains.{name}"
    body = _mapping(body, where)
    _keys(body, where, required={"description", "columns"})
    if not isinstance(body["columns"], list):
        raise CatalogError(f"{where}.columns: expected a list")
    columns = tuple(_column(item, f"{where}.columns") for item in body["columns"])
    for ref in columns:
        if ref.table != "dim_date":
            raise CatalogError(f"{where}.columns: {ref} is not a dim_date column")
    if len(set(columns)) != len(columns):
        raise CatalogError(f"{where}.columns: repeated column")
    return Grain(name, _text(body["description"], f"{where}.description"), columns)


def _dimension(name: str, body: Any) -> Dimension:
    where = f"dimensions.{name}"
    body = _mapping(body, where)
    _keys(body, where, required={"description", "column", "type"}, optional=frozenset({"values"}))
    dim_type = _choice(body["type"], DIMENSION_TYPES, f"{where}.type")
    values = None
    if "values" in body:
        raw = body["values"]
        expected = bool if dim_type == "boolean" else str
        if not isinstance(raw, list) or not raw:
            raise CatalogError(f"{where}.values: expected a non-empty list")
        if any(type(item) is not expected for item in raw) or len(set(raw)) != len(raw):
            raise CatalogError(f"{where}.values: expected distinct {dim_type} values")
        values = tuple(raw)
    return Dimension(
        name=name,
        description=_text(body["description"], f"{where}.description"),
        column=_column(body["column"], f"{where}.column"),
        type=dim_type,
        values=values,
    )


def _operator(name: str, body: Any) -> Operator:
    where = f"operators.{name}"
    if name not in OPERATOR_ARITY:
        raise CatalogError(f"{where}: the compiler cannot render this operator")
    return Operator(name, _text(body, where), OPERATOR_ARITY[name])


def _metric(name: str, body: Any) -> Metric:
    where = f"metrics.{name}"
    body = _mapping(body, where)
    kind = _choice(body.get("kind"), tuple(_KIND_KEYS), f"{where}.kind")
    _keys(body, where, required=_COMMON_METRIC_KEYS | _KIND_KEYS[kind])
    dims = body["dimensions"]
    if not isinstance(dims, list) or any(not isinstance(d, str) for d in dims):
        raise CatalogError(f"{where}.dimensions: expected a list of dimension names")
    if len(set(dims)) != len(dims):
        raise CatalogError(f"{where}.dimensions: repeated dimension")
    fields: dict[str, Any] = {
        "name": name,
        "description": _text(body["description"], f"{where}.description"),
        "kind": kind,
        "unit": _choice(body["unit"], UNITS, f"{where}.unit"),
        "dimensions": tuple(dims),
    }
    if kind in ("sum", "count_distinct"):
        fields["column"] = _column(body["column"], f"{where}.column")
    if kind == "sum":
        fields["sign"] = _choice(body["sign"], _SIGNS, f"{where}.sign")
    if kind == "count_distinct":
        fields["counts"] = _choice(body["counts"], _COUNTS, f"{where}.counts")
    for key in ("numerator", "denominator", "measure", "entity", "condition"):
        if key in body:
            if not isinstance(body[key], str):
                raise CatalogError(f"{where}.{key}: expected a name")
            fields[key] = body[key]
    if kind == "top_n_share":
        n = body["n"]
        if type(n) is not int or not 1 <= n <= MAX_TOP_N:
            raise CatalogError(f"{where}.n: expected an integer from 1 to {MAX_TOP_N}")
        fields["n"] = n
    return Metric(**fields)


def _check_names_disjoint(grains, dimensions, metrics) -> None:
    grain_columns = {ref.column for grain in grains.values() for ref in grain.columns}
    groups = {
        "grain": set(grains),
        "grain column": grain_columns,
        "dimension": set(dimensions),
        "metric": set(metrics),
    }
    labels = list(groups)
    for i, first in enumerate(labels):
        for second in labels[i + 1 :]:
            if first == "grain" and second == "grain column":
                continue  # a grain may be named after its finest column
            clash = sorted(groups[first] & groups[second])
            if clash:
                raise CatalogError(f"names used as both {first} and {second}: {', '.join(clash)}")


def _check_metric_references(metric: Metric, metrics, dimensions) -> None:
    where = f"metrics.{metric.name}"
    for dim in metric.dimensions:
        if dim not in dimensions:
            raise CatalogError(f"{where}.dimensions: unknown dimension {dim}")

    def simple_metric(name: str | None, key: str, kinds: tuple[str, ...]) -> None:
        target = metrics.get(name)
        if target is None or target.kind not in kinds:
            raise CatalogError(f"{where}.{key}: expected a metric of kind {' or '.join(kinds)}")

    def within(name: str | None) -> None:
        extra = sorted(set(metric.dimensions) - set(metrics[name].dimensions))
        if extra:
            raise CatalogError(f"{where}.dimensions: not allowed for {name}: {', '.join(extra)}")

    if metric.kind == "ratio":
        simple_metric(metric.numerator, "numerator", ("sum", "count_distinct"))
        simple_metric(metric.denominator, "denominator", ("sum", "count_distinct"))
        within(metric.numerator)
        within(metric.denominator)
    if metric.kind in ("top_n_share", "share_where"):
        simple_metric(metric.measure, "measure", ("sum",))
        within(metric.measure)
    if metric.kind == "top_n_share":
        if metric.entity not in dimensions:
            raise CatalogError(f"{where}.entity: unknown dimension {metric.entity}")
        if metric.entity in metric.dimensions:
            raise CatalogError(f"{where}.dimensions: cannot include its own entity")
    if metric.kind == "share_where":
        condition = dimensions.get(metric.condition)
        if condition is None or condition.type != "boolean":
            raise CatalogError(f"{where}.condition: expected a boolean dimension")
        if metric.condition in metric.dimensions:
            raise CatalogError(f"{where}.dimensions: cannot include its own condition")
