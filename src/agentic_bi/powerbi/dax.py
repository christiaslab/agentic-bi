"""Load ``catalog/dax.yaml``: one DAX expression per catalog metric (ADR-0004 point 2).

The loader is strict, like the metrics catalog's: the file's metric names must equal the
catalog's, each entry holds exactly ``comment`` and ``expression``, and both are non-empty
text. A metric added to ``catalog/metrics.yaml`` without its DAX, or DAX for a metric the
catalog does not hold, is a ``PowerBIError``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from agentic_bi.semantic import Catalog, default_catalog

from . import ROOT, PowerBIError

DEFAULT_DAX_PATH = ROOT / "catalog" / "dax.yaml"
DAX_VERSION = 1
_TOP_KEYS = {"version", "metrics"}
_ENTRY_KEYS = {"comment", "expression"}


@dataclass(frozen=True)
class DaxMeasure:
    """The DAX of one catalog metric, with the note on how it follows the metric's SQL rule."""

    name: str
    comment: str
    expression: str


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PowerBIError(f"{where} must be non-empty text")
    if "\r" in value or "\t" in value:
        raise PowerBIError(f"{where} must not hold a carriage return or a tab")
    return value.strip()


def parse_dax(data: Any, catalog: Catalog) -> MappingProxyType:
    """Check decoded YAML against the catalog; return the measures in catalog order."""
    if not isinstance(data, dict):
        raise PowerBIError("the DAX file must be a mapping")
    keys = set(data)
    if keys != _TOP_KEYS:
        raise PowerBIError(
            f"the DAX file must hold exactly {sorted(_TOP_KEYS)}; it holds {sorted(map(str, keys))}"
        )
    if type(data["version"]) is not int or data["version"] != DAX_VERSION:
        raise PowerBIError(f"version must be {DAX_VERSION}, not {data['version']!r}")
    entries = data["metrics"]
    if not isinstance(entries, dict):
        raise PowerBIError("metrics must be a mapping of metric name to entry")
    missing = [name for name in catalog.metrics if name not in entries]
    extra = [name for name in entries if name not in catalog.metrics]
    if missing or extra:
        raise PowerBIError(
            "catalog/dax.yaml and catalog/metrics.yaml must list the same metrics; "
            f"missing: {missing or 'none'}, extra: {extra or 'none'}"
        )
    measures = {}
    for name in catalog.metrics:
        entry = entries[name]
        if not isinstance(entry, dict) or set(entry) != _ENTRY_KEYS:
            found = sorted(map(str, entry)) if isinstance(entry, dict) else type(entry).__name__
            raise PowerBIError(
                f"metrics.{name} must hold exactly {sorted(_ENTRY_KEYS)}; it holds {found}"
            )
        measures[name] = DaxMeasure(
            name=name,
            comment=_text(entry["comment"], f"metrics.{name}.comment"),
            expression=_text(entry["expression"], f"metrics.{name}.expression"),
        )
    return MappingProxyType(measures)


def load_dax(path: Path | str | None = None, catalog: Catalog | None = None) -> MappingProxyType:
    """Read and check the DAX file; the project's own when ``path`` is None."""
    source = Path(path) if path is not None else DEFAULT_DAX_PATH
    try:
        data = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PowerBIError(f"cannot read the DAX file at {source}: {exc}") from exc
    return parse_dax(data, catalog if catalog is not None else default_catalog())
