"""docs/catalog.md must keep up with catalog/metrics.yaml."""

from pathlib import Path

import pytest

from agentic_bi.semantic.catalog import DEFAULT_CATALOG_PATH, load_catalog

GUIDE = Path(__file__).resolve().parents[2] / "docs" / "catalog.md"


def test_guide_names_every_metric_dimension_grain_and_operator() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    catalog = load_catalog()
    for names in (catalog.metrics, catalog.dimensions, catalog.grains, catalog.operators):
        for name in names:
            assert f"`{name}`" in text, name


def test_guide_table_of_allowed_dimensions_matches_the_catalog() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    catalog = load_catalog()
    header = "| Dimension | " + " | ".join(catalog.metrics) + " |"
    assert header in text
    rows = text.split(header, 1)[1].split("\n\n", 1)[0].splitlines()[2:]
    table = {}
    for row in rows:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        table[cells[0].strip("`")] = [cell == "yes" for cell in cells[1:]]
    assert set(table) == set(catalog.dimensions)
    for dimension, marks in table.items():
        expected = [dimension in metric.dimensions for metric in catalog.metrics.values()]
        assert marks == expected, dimension


@pytest.mark.parametrize("path", [GUIDE, DEFAULT_CATALOG_PATH])
def test_no_em_or_en_dash(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    em_dash, en_dash = chr(0x2014), chr(0x2013)
    assert em_dash not in text and en_dash not in text
