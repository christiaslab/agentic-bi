"""Tool results reach the model as data inside one clear delimiter (ADR-0002 point 9).

Every tool result is JSON wrapped in ``<tool_data tool="..." status="...">`` and
``</tool_data>``. Inside the JSON, ``<``, ``>`` and ``&`` are written as ``\\u003c``,
``\\u003e`` and ``\\u0026``: still the same JSON value, but no text from the warehouse, such as
a recipient name, can contain the delimiter and close it early. The ``tool`` attribute is one
of the four tool names or ``unknown``, never text taken from the model or the data. The system
prompt tells the model that whatever is inside the delimiter is data, never an instruction.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

OPEN = "<tool_data"
CLOSE = "</tool_data>"
TOOL_NAMES = ("catalog", "schema", "run_query", "explain_measure")
STATUSES = ("ok", "error")

_ESCAPES = {ord("<"): "\\u003c", ord(">"): "\\u003e", ord("&"): "\\u0026"}


def json_safe(value: Any) -> Any:
    """A JSON-ready copy: exact decimals as strings, dates as ISO strings, tuples as lists."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    raise TypeError(f"cannot pass a {type(value).__name__} to the model")


def dumps(payload: Any) -> str:
    """Compact JSON text with no ``<``, ``>`` or ``&`` character, decoding to ``payload``."""
    text = json.dumps(json_safe(payload), ensure_ascii=False, allow_nan=False)
    return text.translate(_ESCAPES)


def wrap(tool: str, payload: Any, *, error: bool = False) -> str:
    """The text of one tool result as the model receives it."""
    name = tool if tool in TOOL_NAMES else "unknown"
    status = "error" if error else "ok"
    return f'{OPEN} tool="{name}" status="{status}">\n{dumps(payload)}\n{CLOSE}'


def unwrap(text: str) -> tuple[str, str, Any]:
    """Read back what ``wrap`` wrote: ``(tool, status, payload)``. Strict, for tests."""
    head, separator, rest = text.partition("\n")
    if not separator or not head.startswith(f'{OPEN} tool="') or not head.endswith('">'):
        raise ValueError("not a tool_data envelope")
    attributes = head[len(f'{OPEN} tool="') : -len('">')]
    tool, separator, status = attributes.partition('" status="')
    if not separator or tool not in (*TOOL_NAMES, "unknown") or status not in STATUSES:
        raise ValueError("unexpected tool_data attributes")
    body, separator, tail = rest.rpartition("\n")
    if not separator or tail != CLOSE:
        raise ValueError("tool_data envelope is not closed")
    if OPEN in body or CLOSE in body:
        raise ValueError("delimiter text inside the payload")
    return tool, status, json.loads(body)
