"""Golden reproduction (S3b): the publisher's golden values through the compiler of ADR-0002.

``evals/golden_queries.yaml`` maps every value of ``docs/golden/golden.json`` to one
structured query, or to the reason the catalog cannot express it (``mapping``). The run
builds the warehouse, compiles and executes each query on the read-only query connection,
and compares the result with the golden value under the rule of its metric kind
(``compare``, ``run``). ``python -m agentic_bi.reproduce`` writes the outcome per value.
Reading guide: docs/reproduction.md.
"""

from .compare import Comparison, ComparisonError, compare, tolerance_for
from .mapping import (
    DEFAULT_GOLDEN,
    DEFAULT_MAPPING,
    Golden,
    GoldenMapping,
    MappedQuery,
    MappingError,
    NotExpressible,
    load_golden,
    load_mapping,
    parse_mapping,
)
from .run import (
    DIFFERS,
    ERROR,
    NOT_EXPRESSIBLE,
    REPRODUCED,
    Outcome,
    SelectionError,
    report,
    reproduce,
    select_value,
    summary,
    write_report,
)

__all__ = [
    "DEFAULT_GOLDEN",
    "DEFAULT_MAPPING",
    "DIFFERS",
    "ERROR",
    "NOT_EXPRESSIBLE",
    "REPRODUCED",
    "Comparison",
    "ComparisonError",
    "Golden",
    "GoldenMapping",
    "MappedQuery",
    "MappingError",
    "NotExpressible",
    "Outcome",
    "SelectionError",
    "compare",
    "load_golden",
    "load_mapping",
    "parse_mapping",
    "report",
    "reproduce",
    "select_value",
    "summary",
    "tolerance_for",
    "write_report",
]
