"""The Power BI path of ADR-0004: the contract tables in Parquet and a generated PBIP model.

``export`` builds the warehouse and writes one Parquet file per contract table to
``data/model/``. ``generate`` writes the Power BI Desktop project in ``powerbi/``: a semantic
model in TMDL, written from ``docs/contracts/star-schema.md``, ``catalog/metrics.yaml`` and
``catalog/dax.yaml``, so the agent and a Power BI report answer from one set of measure
definitions. Reading guide: docs/powerbi.md.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


class PowerBIError(Exception):
    """A check of the Power BI path failed: the export, the DAX catalog or the generator."""


def inside(path: Path | str, root: Path | str) -> Path:
    """``path`` resolved, refused unless it stays inside ``root`` (no ``..`` or symlink escape)."""
    resolved = Path(path).resolve()
    base = Path(root).resolve()
    if resolved != base and base not in resolved.parents:
        raise PowerBIError(f"{path} resolves to {resolved}, outside {base}")
    return resolved
