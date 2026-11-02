"""The metrics catalog: the ADR-0002 rules it must fix, and the loader's strictness."""

import copy
from pathlib import Path

import pytest
import yaml

from agentic_bi.semantic import contract
from agentic_bi.semantic.catalog import (
    DEFAULT_CATALOG_PATH,
    ColumnRef,
    load_catalog,
    parse_catalog,
)
from agentic_bi.semantic.errors import CatalogError

REQUIRED_METRICS = {
    "obligations",
    "award_count",
    "avg_award_value",
    "supplier_concentration_top10",
    "competition_rate",
}


@pytest.fixture(scope="module")
def raw() -> dict:
    return yaml.safe_load(DEFAULT_CATALOG_PATH.read_text(encoding="utf-8"))


def test_catalog_defines_the_five_metrics_each_described_with_allowed_dimensions() -> None:
    catalog = load_catalog()
    assert set(catalog.metrics) == REQUIRED_METRICS
    for metric in catalog.metrics.values():
        assert len(metric.description.split()) >= 15, metric.name
        assert metric.dimensions, metric.name
        assert set(metric.dimensions) <= set(catalog.dimensions)


def test_catalog_fixes_the_metric_rules_of_adr_0002() -> None:
    catalog = load_catalog()
    obligations = catalog.metrics["obligations"]
    assert (obligations.kind, obligations.sign) == ("sum", "net")
    assert obligations.column == ColumnRef(contract.FACT_TABLE, "obligation_amount")

    award_count = catalog.metrics["award_count"]
    assert (award_count.kind, award_count.counts) == ("count_distinct", "active_in_period")
    assert award_count.column == ColumnRef(contract.FACT_TABLE, "award_key")

    average = catalog.metrics["avg_award_value"]
    assert (average.kind, average.numerator, average.denominator) == (
        "ratio",
        "obligations",
        "award_count",
    )

    concentration = catalog.metrics["supplier_concentration_top10"]
    assert (concentration.kind, concentration.measure, concentration.n) == (
        "top_n_share",
        "obligations",
        10,
    )
    recipient = catalog.dimensions[concentration.entity]
    assert recipient.column == ColumnRef("fct_contract_transaction", "reporting_uei")
    assert "parent" in concentration.description and "own UEI" in concentration.description

    rate = catalog.metrics["competition_rate"]
    assert (rate.kind, rate.measure) == ("share_where", "obligations")
    assert catalog.dimensions[rate.condition].column == ColumnRef("dim_competition", "is_competed")


def test_catalog_months_are_fiscal_and_carry_their_fiscal_year() -> None:
    grains = load_catalog().grains
    assert [str(ref) for ref in grains["fiscal_month"].columns] == [
        "dim_date.fiscal_year",
        "dim_date.fiscal_month",
    ]
    assert "Month 1 is October" in grains["fiscal_month"].description
    assert grains["total"].columns == ()


def test_metrics_cannot_be_sliced_by_their_own_entity_or_condition() -> None:
    metrics = load_catalog().metrics
    assert "recipient" not in metrics["supplier_concentration_top10"].dimensions
    assert "competed" not in metrics["competition_rate"].dimensions
    assert "competition" not in metrics["competition_rate"].dimensions


def test_catalog_columns_are_all_in_the_contract() -> None:
    catalog = load_catalog()
    refs = [dim.column for dim in catalog.dimensions.values()]
    refs += [ref for grain in catalog.grains.values() for ref in grain.columns]
    refs += [m.column for m in catalog.metrics.values() if m.column is not None]
    for ref in refs:
        assert contract.has_column(ref.table, ref.column), ref


def test_loaded_catalog_is_immutable() -> None:
    catalog = load_catalog()
    with pytest.raises(TypeError):
        catalog.metrics["invented"] = catalog.metrics["obligations"]


def _set(path: str, value):
    def mutate(data: dict) -> None:
        *parents, last = path.split("/")
        node = data
        for key in parents:
            node = node[key]
        node[last] = value

    return mutate


def _delete(path: str):
    def mutate(data: dict) -> None:
        *parents, last = path.split("/")
        node = data
        for key in parents:
            node = node[key]
        del node[last]

    return mutate


INVALID_CATALOGS = [
    (
        "column-outside-contract",
        _set("metrics/obligations/column", "fct_contract_transaction.amount"),
        "contract",
    ),
    (
        "column-with-sql",
        _set("dimensions/psc/column", "dim_psc.psc_code; drop table x"),
        "contract",
    ),
    ("unknown-key", _set("metrics/obligations/rounding", 2), "unknown key"),
    ("gross-sign", _set("metrics/obligations/sign", "gross"), "sign"),
    (
        "unknown-dimension",
        _set("metrics/obligations/dimensions", ["award_type", "vendor"]),
        "unknown dimension",
    ),
    (
        "repeated-dimension",
        _set("metrics/award_count/dimensions", ["award_type", "award_type"]),
        "repeated",
    ),
    (
        "rate-by-own-condition",
        _set("metrics/competition_rate/dimensions", ["competed"]),
        "own condition",
    ),
    (
        "share-by-own-entity",
        _set("metrics/supplier_concentration_top10/dimensions", ["recipient"]),
        "own entity",
    ),
    (
        "ratio-of-a-share",
        _set("metrics/avg_award_value/denominator", "competition_rate"),
        "denominator",
    ),
    ("top-n-as-text", _set("metrics/supplier_concentration_top10/n", "10"), "integer"),
    ("unrenderable-operator", _set("operators/like", "pattern match"), "cannot render"),
    ("name-not-identifier", _set("dimensions/Bad-Name", {}), "lowercase identifier"),
    ("no-total-grain", _delete("grains/total"), "total"),
    (
        "grain-off-dim-date",
        _set("grains/fiscal_year/columns", ["fct_contract_transaction.action_date"]),
        "dim_date",
    ),
    ("wrong-version", _set("version", 2), "version"),
]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [pytest.param(mutate, message, id=case) for case, mutate, message in INVALID_CATALOGS],
)
def test_loader_rejects_an_invalid_catalog(raw: dict, mutate, message: str) -> None:
    data = copy.deepcopy(raw)
    mutate(data)
    with pytest.raises(CatalogError, match=message):
        parse_catalog(data)


def test_loader_rejects_a_dimension_named_like_a_grain_column(raw: dict) -> None:
    data = copy.deepcopy(raw)
    data["dimensions"]["fiscal_month"] = copy.deepcopy(data["dimensions"]["psc"])
    with pytest.raises(CatalogError, match="both"):
        parse_catalog(data)


def test_loader_reports_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="cannot read"):
        load_catalog(tmp_path / "missing.yaml")
