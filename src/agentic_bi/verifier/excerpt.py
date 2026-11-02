"""The catalog excerpt the judge reads: the catalog's words, never its tables or columns.

The judge decides intent against the catalog's definitions (ADR-0002 point 8), so it needs
every metric's rule, every dimension's meaning and values, the grains, the operators and the
slice the warehouse holds. It never needs a physical name: the verifier judges intent, not
SQL, and like the schema tool (ADR-0002 point 7) the excerpt shows no table or column.
"""

from __future__ import annotations

from typing import Any

from agentic_bi.ingest import spec
from agentic_bi.semantic import Catalog, default_catalog

from .checks import MAX_LIMIT, MIN_LIMIT, ORDER_DIRECTIONS, SLICE


def catalog_excerpt(catalog: Catalog | None = None) -> dict[str, Any]:
    """The catalog as plain data for the judge: names, descriptions and allowed values."""
    catalog = catalog if catalog is not None else default_catalog()
    return {
        "slice": {
            "agency": spec.AGENCY_NAME,
            "award_types": list(spec.AWARD_TYPE_CODES),
            "fiscal_year": spec.FISCAL_YEAR,
            "first_date": SLICE.start.isoformat(),
            "last_date": SLICE.end.isoformat(),
            "rule": (
                "The warehouse holds these contract transactions and nothing else: no other "
                "agency, no grants or other award types, no other fiscal year. A null "
                "time_range means the whole slice."
            ),
        },
        "metrics": [
            {
                "name": metric.name,
                "description": metric.description,
                "unit": metric.unit,
                "allowed_dimensions": list(metric.dimensions),
            }
            for metric in catalog.metrics.values()
        ],
        "dimensions": [
            {
                "name": dimension.name,
                "description": dimension.description,
                "type": dimension.type,
                "values": None if dimension.values is None else list(dimension.values),
            }
            for dimension in catalog.dimensions.values()
        ],
        "grains": [
            {"name": grain.name, "description": grain.description}
            for grain in catalog.grains.values()
        ],
        "operators": [
            {"name": operator.name, "description": operator.description}
            for operator in catalog.operators.values()
        ],
        "ranking": {
            "fields": ["order_by", "limit"],
            "rule": (
                "order_by is {field, direction}: field is the metric or a requested dimension, "
                f"direction is one of {', '.join(ORDER_DIRECTIONS)}. limit is an integer from "
                f"{MIN_LIMIT} to {MAX_LIMIT} and keeps the first rows after ordering. A query "
                "without them returns every group in a fixed order."
            ),
        },
    }
