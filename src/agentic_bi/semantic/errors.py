"""Errors of the semantic layer.

Two families. A ``CatalogError`` means the catalog file itself is wrong: an owner's mistake,
found when the catalog loads. A ``QueryRefusal`` means a structured query asked for something
outside the catalog or in the wrong shape (ADR-0002 point 3). The agent reports a refusal; it
never improvises a substitute. Every refusal carries a stable ``code`` and the ``field`` of the
query it concerns, written as a path such as ``filters[0].op``.
"""

from __future__ import annotations


class SemanticError(Exception):
    """Base of every error raised by ``agentic_bi.semantic``."""


class CatalogError(SemanticError):
    """The metrics catalog is invalid; raised while it loads, never for a query."""


class QueryRefusal(SemanticError):
    """A structured query the compiler refuses. Subclasses name the reason."""

    code = "refused"

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


class QueryValidationError(QueryRefusal):
    """The query does not have the shape of the structured-query JSON schema."""

    code = "invalid_query"


class MalformedTimeRangeError(QueryValidationError):
    """The time range is not an object of two ISO dates with start on or before end."""

    code = "malformed_time_range"


class UnknownMetricError(QueryRefusal):
    """The metric is not in the catalog."""

    code = "unknown_metric"


class UnknownDimensionError(QueryRefusal):
    """A requested dimension is not in the catalog."""

    code = "unknown_dimension"


class DimensionNotAllowedError(QueryRefusal):
    """The dimension exists but the metric may not be grouped or filtered by it."""

    code = "dimension_not_allowed"


class UnknownGrainError(QueryRefusal):
    """The time grain is not in the catalog."""

    code = "unknown_grain"


class UnknownOperatorError(QueryRefusal):
    """A filter operator is not in the catalog."""

    code = "unknown_operator"


class FilterFieldNotDimensionError(QueryRefusal):
    """A filter names a field that is not a catalog dimension."""

    code = "filter_field_not_dimension"


class InvalidFilterValueError(QueryRefusal):
    """A filter value has the wrong type or arity, or lies outside the dimension's values."""

    code = "invalid_filter_value"


class QueryLimitError(SemanticError):
    """A compiled query hit a limit of the query connection (ADR-0002 point 6)."""


class RowLimitExceededError(QueryLimitError):
    """The result has more rows than the row limit allows."""


class QueryTimeoutError(QueryLimitError):
    """The query ran longer than the timeout and was interrupted."""
