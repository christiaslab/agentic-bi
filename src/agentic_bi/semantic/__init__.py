"""The semantic layer of ADR-0002: metrics catalog, structured query and compiler.

A structured query goes in (``parse_query``), the compiler renders parameterised SQL over the
star-schema contract from the catalog (``compile``), and ``execute`` runs it on a read-only,
locked query connection (``open_query_connection``). Anything outside the catalog is a
``QueryRefusal``. Reading guide: docs/catalog.md.
"""

from .catalog import Catalog, default_catalog, load_catalog
from .compiler import CompiledQuery, compile
from .errors import CatalogError, QueryLimitError, QueryRefusal, SemanticError
from .query import QUERY_JSON_SCHEMA, StructuredQuery, parse_query, query_json_schema
from .runner import QueryResult, execute, open_query_connection

__all__ = [
    "QUERY_JSON_SCHEMA",
    "Catalog",
    "CatalogError",
    "CompiledQuery",
    "QueryLimitError",
    "QueryRefusal",
    "QueryResult",
    "SemanticError",
    "StructuredQuery",
    "compile",
    "default_catalog",
    "execute",
    "load_catalog",
    "open_query_connection",
    "parse_query",
    "query_json_schema",
]
