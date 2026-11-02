"""Small writers for TMDL text: names, references, descriptions and expressions.

TMDL indents with tabs, one level per object depth. An object line is ``keyword name``,
optionally ``= value``; its properties are ``name: value`` one level deeper. A name that is
not a plain identifier is wrapped in single quotes, with a quote inside doubled. A
description is written as ``///`` lines right above the object. A multi-line expression
starts after ``=`` at the end of its line and continues two levels deeper than that line,
as Power BI Desktop writes it.
"""

from __future__ import annotations

import re
import uuid

from . import PowerBIError

INDENT = "\t"
_PLAIN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# A fixed namespace, so every lineage tag is the same on every run and every machine.
_LINEAGE_NAMESPACE = uuid.UUID("5d0d2f4e-6a0b-4c55-9a7c-3a8e6c0b2f11")


def name(value: str) -> str:
    """A TMDL object name: plain identifiers as they are, anything else in single quotes."""
    if not value or "\n" in value or "\r" in value or "\t" in value:
        raise PowerBIError(f"{value!r} cannot be a TMDL name")
    if _PLAIN.fullmatch(value):
        return value
    return "'" + value.replace("'", "''") + "'"


def column_ref(table: str, column: str) -> str:
    """``table.column`` as a relationship property writes it, each part quoted when needed."""
    return f"{name(table)}.{name(column)}"


def dax_column(table: str, column: str) -> str:
    """``table[column]`` in DAX: the table quoted when needed, ``]`` doubled in the column."""
    table_part = table if _PLAIN.fullmatch(table) else "'" + table.replace("'", "''") + "'"
    return f"{table_part}[{column.replace(']', ']]')}]"


def lineage_tag(*path: str) -> str:
    """A stable UUID for one object, derived from its kind and names."""
    return str(uuid.uuid5(_LINEAGE_NAMESPACE, "/".join(("agentic-bi", *path))))


def description(text: str, depth: int) -> list[str]:
    """``///`` lines for an object at ``depth``; one line per line of ``text``."""
    lines = text.strip().splitlines()
    if not lines:
        raise PowerBIError("a description must not be empty")
    return [f"{INDENT * depth}/// {line.strip()}".rstrip() for line in lines]


def declaration(head: str, expression: str, depth: int) -> list[str]:
    """``head = expression`` at ``depth``: one line, or the expression two levels deeper."""
    body = expression.strip("\n")
    if "\t" in body or "\r" in body:
        raise PowerBIError(f"the expression of {head!r} must use spaces and line feeds only")
    if "\n" not in body:
        return [f"{INDENT * depth}{head} = {body.strip()}"]
    inner = INDENT * (depth + 2)
    lines = [f"{INDENT * depth}{head} ="]
    for line in body.splitlines():
        if not line.strip():
            raise PowerBIError(f"the expression of {head!r} must not hold a blank line")
        lines.append(f"{inner}{line.rstrip()}")
    return lines


def to_crlf_bytes(text: str) -> bytes:
    """UTF-8 without a byte-order mark, every line ended by CRLF."""
    if "\r" in text:
        raise PowerBIError("generated text must hold line feeds only before conversion")
    if not text.endswith("\n"):
        text += "\n"
    return text.replace("\n", "\r\n").encode("utf-8")
