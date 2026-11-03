"""Load the golden values and their mapping to structured queries, and check both strictly.

``docs/golden/golden.json`` holds the publisher's values (S1). ``evals/golden_queries.yaml``
maps each of them to one structured query of the S3a schema, or to the reason the catalog
cannot express it. The loader checks the shape only: every golden id appears exactly once,
each entry is either a query or a not-expressible reason, and every query has the shape of
the JSON schema (``parse_query``). Whether the compiler accepts a query, and what it returns,
is the run's question (``run.py``), so a compiler failure shows up as that value's outcome
and not as a broken mapping.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agentic_bi.semantic import QueryRefusal, StructuredQuery, parse_query

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_GOLDEN = ROOT / "docs" / "golden" / "golden.json"
DEFAULT_MAPPING = ROOT / "evals" / "golden_queries.yaml"

MAPPING_VERSION = 1
GOLDEN_SCHEMA_VERSION = 1
DISPOSITIONS = ("candidate_catalog_change", "out_of_scope_by_design")
TAGS = ("owner_api", "derived")

_QUERY_KEYS = {"id", "query", "row", "known_difference", "note"}
_NOT_EXPRESSIBLE_KEYS = {"id", "not_expressible", "disposition", "attempt", "note"}
_ATTEMPT_KEYS = {"query", "refusal"}


class MappingError(ValueError):
    """The golden file or the mapping file is malformed or out of step with the other."""


@dataclass(frozen=True)
class GoldenValue:
    """One value of docs/golden/golden.json: a decimal string or an integer, never a float."""

    id: str
    tag: str
    value: str | int
    unit: str
    description: str


@dataclass(frozen=True)
class Golden:
    capture_date: str
    values: tuple[GoldenValue, ...]

    def by_id(self) -> dict[str, GoldenValue]:
        return {value.id: value for value in self.values}


@dataclass(frozen=True)
class MappedQuery:
    """A golden value with the structured query that should reproduce it."""

    id: str
    query: StructuredQuery
    # output column -> value selecting the one row of a grouped result; empty for one-row results
    row: Mapping[str, Any]
    known_difference: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class Attempt:
    """The nearest structured query to a not-expressible value, and the refusal it gets."""

    query: Mapping[str, Any]
    refusal: str


@dataclass(frozen=True)
class NotExpressible:
    """A golden value no catalog query gives, with the reason in one line."""

    id: str
    reason: str
    disposition: str
    attempt: Attempt | None = None
    note: str | None = None


Entry = MappedQuery | NotExpressible


@dataclass(frozen=True)
class GoldenMapping:
    golden_capture_date: str
    entries: tuple[Entry, ...]

    def by_id(self) -> dict[str, Entry]:
        return {entry.id: entry for entry in self.entries}


def load_golden(path: str | Path | None = None) -> Golden:
    """Read the golden file and check the fields the reproduction relies on."""
    path = Path(path) if path is not None else DEFAULT_GOLDEN
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping) or data.get("schema_version") != GOLDEN_SCHEMA_VERSION:
        raise MappingError(f"{path}: expected a golden file of schema_version 1")
    capture_date = data.get("capture_date_utc")
    if not isinstance(capture_date, str):
        raise MappingError(f"{path}: capture_date_utc is missing")
    raw_values = data.get("values")
    if not isinstance(raw_values, list) or not raw_values:
        raise MappingError(f"{path}: values must be a non-empty list")
    values = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_values):
        where = f"{path}: values[{i}]"
        if not isinstance(raw, Mapping):
            raise MappingError(f"{where}: expected an object")
        value_id = raw.get("id")
        if not isinstance(value_id, str) or not value_id:
            raise MappingError(f"{where}: id must be a non-empty string")
        if value_id in seen:
            raise MappingError(f"{where}: id {value_id!r} appears twice")
        seen.add(value_id)
        if raw.get("tag") not in TAGS:
            raise MappingError(f"{where}: tag must be one of {', '.join(TAGS)}")
        value = raw.get("value")
        # amounts and ratios are decimal strings, counts integers (the golden file's format)
        if isinstance(value, bool) or not isinstance(value, str | int):
            raise MappingError(f"{where}: value must be a decimal string or an integer")
        values.append(
            GoldenValue(
                id=value_id,
                tag=raw["tag"],
                value=value,
                unit=str(raw.get("unit", "")),
                description=str(raw.get("description", "")),
            )
        )
    return Golden(capture_date=capture_date, values=tuple(values))


def load_mapping(path: str | Path | None = None, golden: Golden | None = None) -> GoldenMapping:
    """Read the mapping file and check it against the golden file, id by id."""
    path = Path(path) if path is not None else DEFAULT_MAPPING
    golden = golden if golden is not None else load_golden()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return parse_mapping(data, golden, source=str(path))


def parse_mapping(data: Any, golden: Golden, *, source: str = "mapping") -> GoldenMapping:
    """Check a decoded mapping document against the golden values."""
    if not isinstance(data, Mapping):
        raise MappingError(f"{source}: expected a mapping at the top level")
    unknown = set(data) - {"version", "golden_capture_date", "values"}
    if unknown:
        raise MappingError(f"{source}: unknown top-level keys {sorted(unknown)}")
    if data.get("version") != MAPPING_VERSION:
        raise MappingError(f"{source}: version must be {MAPPING_VERSION}")
    if data.get("golden_capture_date") != golden.capture_date:
        raise MappingError(
            f"{source}: golden_capture_date {data.get('golden_capture_date')!r} is not the "
            f"golden file's capture date {golden.capture_date!r}"
        )
    raw_entries = data.get("values")
    if not isinstance(raw_entries, list):
        raise MappingError(f"{source}: values must be a list")

    known_ids = {value.id for value in golden.values}
    entries: dict[str, Entry] = {}
    for i, raw in enumerate(raw_entries):
        where = f"{source}: values[{i}]"
        entry = _entry(raw, where)
        if entry.id not in known_ids:
            raise MappingError(f"{where}: {entry.id!r} is not a golden id")
        if entry.id in entries:
            raise MappingError(f"{where}: {entry.id!r} is mapped twice")
        entries[entry.id] = entry
    missing = [value.id for value in golden.values if value.id not in entries]
    if missing:
        raise MappingError(f"{source}: golden ids not mapped: {', '.join(missing)}")
    # golden order, so reports read in the order of docs/golden/golden.json
    return GoldenMapping(
        golden_capture_date=golden.capture_date,
        entries=tuple(entries[value.id] for value in golden.values),
    )


def _entry(raw: Any, where: str) -> Entry:
    if not isinstance(raw, Mapping):
        raise MappingError(f"{where}: expected a mapping")
    value_id = raw.get("id")
    if not isinstance(value_id, str) or not value_id:
        raise MappingError(f"{where}: id must be a non-empty string")
    where = f"{where} ({value_id})"
    has_query, has_reason = "query" in raw, "not_expressible" in raw
    if has_query == has_reason:
        raise MappingError(f"{where}: give exactly one of query and not_expressible")
    note = _optional_text(raw, "note", where)
    if has_query:
        _check_keys(raw, _QUERY_KEYS, where)
        try:
            query = parse_query(raw["query"])
        except QueryRefusal as exc:
            raise MappingError(f"{where}: query is not a structured query: {exc}") from exc
        return MappedQuery(
            id=value_id,
            query=query,
            row=_row(raw.get("row", {}), where),
            known_difference=_optional_text(raw, "known_difference", where),
            note=note,
        )
    _check_keys(raw, _NOT_EXPRESSIBLE_KEYS, where)
    reason = _optional_text(raw, "not_expressible", where)
    if reason is None or "\n" in reason:
        raise MappingError(f"{where}: not_expressible must be a one-line reason")
    disposition = raw.get("disposition")
    if disposition not in DISPOSITIONS:
        raise MappingError(f"{where}: disposition must be one of {', '.join(DISPOSITIONS)}")
    return NotExpressible(
        id=value_id,
        reason=reason,
        disposition=disposition,
        attempt=_attempt(raw.get("attempt"), where),
        note=note,
    )


def _check_keys(raw: Mapping[str, Any], allowed: set[str], where: str) -> None:
    unknown = set(raw) - allowed
    if unknown:
        raise MappingError(f"{where}: unknown keys {sorted(unknown)}")


def _optional_text(raw: Mapping[str, Any], key: str, where: str) -> str | None:
    if key not in raw:
        return None
    value = raw[key]
    if not isinstance(value, str) or not value.strip():
        raise MappingError(f"{where}: {key} must be non-empty text")
    return value.strip()


def _row(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MappingError(f"{where}: row must be a mapping of output column to value")
    for column, selector in value.items():
        if not isinstance(column, str) or not column:
            raise MappingError(f"{where}: row keys must be output column names")
        if selector is not None and type(selector) not in (str, int, bool):
            raise MappingError(f"{where}: row.{column} must be text, an integer, a boolean or null")
    return dict(value)


def _attempt(value: Any, where: str) -> Attempt | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != _ATTEMPT_KEYS:
        raise MappingError(f"{where}: attempt must have exactly query and refusal")
    if not isinstance(value["query"], Mapping):
        raise MappingError(f"{where}: attempt.query must be a mapping")
    if not isinstance(value["refusal"], str) or not value["refusal"]:
        raise MappingError(f"{where}: attempt.refusal must be a refusal code")
    return Attempt(query=dict(value["query"]), refusal=value["refusal"])


__all__ = [
    "DEFAULT_GOLDEN",
    "DEFAULT_MAPPING",
    "DISPOSITIONS",
    "Attempt",
    "Entry",
    "Golden",
    "GoldenMapping",
    "GoldenValue",
    "MappedQuery",
    "MappingError",
    "NotExpressible",
    "load_golden",
    "load_mapping",
    "parse_mapping",
]
