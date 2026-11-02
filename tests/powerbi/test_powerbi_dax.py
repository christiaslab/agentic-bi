"""catalog/dax.yaml: one DAX expression per catalog metric, each tied to its SQL rule."""

from __future__ import annotations

import copy
import re

import pytest
import yaml

from agentic_bi.powerbi import PowerBIError
from agentic_bi.powerbi.dax import DEFAULT_DAX_PATH, load_dax, parse_dax
from agentic_bi.semantic import default_catalog

CATALOG = default_catalog()


@pytest.fixture
def data() -> dict:
    return yaml.safe_load(DEFAULT_DAX_PATH.read_text(encoding="utf-8"))


def _squash(text: str) -> str:
    """DAX without whitespace, to match a shape whatever the line breaks."""
    return re.sub(r"\s+", "", text)


def test_the_file_holds_one_entry_per_catalog_metric_in_catalog_order() -> None:
    measures = load_dax()
    assert list(measures) == list(CATALOG.metrics)
    for name, measure in measures.items():
        assert measure.name == name
        assert measure.expression and measure.comment


def test_a_missing_metric_is_refused(data) -> None:
    del data["metrics"]["competition_rate"]
    with pytest.raises(PowerBIError, match=r"missing: \['competition_rate'\]"):
        parse_dax(data, CATALOG)


def test_an_extra_metric_is_refused(data) -> None:
    data["metrics"]["new_awards"] = copy.deepcopy(data["metrics"]["award_count"])
    with pytest.raises(PowerBIError, match=r"extra: \['new_awards'\]"):
        parse_dax(data, CATALOG)


def test_an_unknown_entry_key_is_refused(data) -> None:
    data["metrics"]["obligations"]["format"] = "#,0"
    with pytest.raises(PowerBIError, match="metrics.obligations must hold exactly"):
        parse_dax(data, CATALOG)


def test_a_missing_entry_key_is_refused(data) -> None:
    del data["metrics"]["obligations"]["comment"]
    with pytest.raises(PowerBIError, match="metrics.obligations must hold exactly"):
        parse_dax(data, CATALOG)


def test_an_unknown_top_level_key_is_refused(data) -> None:
    data["measures"] = {}
    with pytest.raises(PowerBIError, match="must hold exactly"):
        parse_dax(data, CATALOG)


@pytest.mark.parametrize("version", [2, "1", True, None])
def test_a_wrong_version_is_refused(data, version) -> None:
    data["version"] = version
    with pytest.raises(PowerBIError, match="version"):
        parse_dax(data, CATALOG)


@pytest.mark.parametrize("value", ["", "   ", None, 42, ["SUM ( x )"]])
def test_an_empty_or_non_text_expression_is_refused(data, value) -> None:
    data["metrics"]["award_count"]["expression"] = value
    with pytest.raises(PowerBIError, match="metrics.award_count.expression"):
        parse_dax(data, CATALOG)


def test_a_tab_in_an_expression_is_refused(data) -> None:
    data["metrics"]["obligations"]["expression"] = "SUM (\tfct_contract_transaction[x] )"
    with pytest.raises(PowerBIError, match="tab"):
        parse_dax(data, CATALOG)


@pytest.mark.parametrize("value", [None, [], "metrics"])
def test_a_malformed_file_is_refused(value) -> None:
    with pytest.raises(PowerBIError):
        parse_dax(value, CATALOG)


def test_an_unreadable_file_is_refused(tmp_path) -> None:
    with pytest.raises(PowerBIError, match="cannot read"):
        load_dax(tmp_path / "missing.yaml")
    broken = tmp_path / "broken.yaml"
    broken.write_text("metrics: [unclosed", encoding="utf-8")
    with pytest.raises(PowerBIError, match="cannot read"):
        load_dax(broken)


# Each metric's DAX must follow the parameters of its catalog entry, so a change to the
# catalog (another column, another n, another condition) fails here until the DAX follows.


def _column(ref) -> str:
    return f"{ref.table}[{ref.column}]"


@pytest.mark.parametrize("name", list(CATALOG.metrics))
def test_the_dax_follows_the_catalog_rule(name) -> None:
    metric = CATALOG.metrics[name]
    dax = _squash(load_dax()[name].expression)
    if metric.kind == "sum":
        assert metric.sign == "net"
        # net: a plain SUM, nothing that drops or flips negative amounts
        assert dax == _squash(f"SUM ( {_column(metric.column)} )")
    elif metric.kind == "count_distinct":
        assert metric.counts == "active_in_period"
        assert dax == _squash(f"DISTINCTCOUNTNOBLANK ( {_column(metric.column)} )")
    elif metric.kind == "ratio":
        assert dax == _squash(f"DIVIDE ( [{metric.numerator}], [{metric.denominator}] )")
    elif metric.kind == "top_n_share":
        entity = _column(CATALOG.dimensions[metric.entity].column)
        assert entity == "fct_contract_transaction[reporting_uei]"
        assert f"VALUES({entity})" in dax
        assert f'"@obligations",[{metric.measure}]' in dax
        assert f"TOPN({metric.n}," in dax
        assert f"[@obligations],DESC,{entity},ASC" in dax
        assert "DIVIDE(COALESCE(_top_10_obligations,0),_all_obligations)" in dax
    elif metric.kind == "share_where":
        condition = _column(CATALOG.dimensions[metric.condition].column)
        assert f"KEEPFILTERS({condition}=TRUE())" in dax
        assert f"CALCULATE([{metric.measure}]," in dax
        assert f"VAR_all_obligations=[{metric.measure}]" in dax
        assert "DIVIDE(COALESCE(_competed_obligations,0),_all_obligations)" in dax
    else:  # pragma: no cover - a new kind needs its own check here
        pytest.fail(f"no DAX check for kind {metric.kind}")


def test_no_measure_takes_an_absolute_value_or_drops_negative_rows() -> None:
    for name, measure in load_dax().items():
        dax = _squash(measure.expression).upper()
        assert "ABS(" not in dax, name
        assert ">0" not in dax and ">=0" not in dax, name


@pytest.mark.parametrize(
    ("name", "words"),
    [
        ("obligations", ["Sign", "negative"]),
        ("award_count", ["Active-in-period", "de-obligation"]),
        ("avg_award_value", ["obligations / nullif(award_count, 0)"]),
        ("supplier_concentration_top10", ["reporting parent", "reporting_uei", "ten largest"]),
        ("competition_rate", ["Competed share", "is_competed", "not competed"]),
    ],
)
def test_each_comment_names_the_rule_it_follows(name, words) -> None:
    comment = load_dax()[name].comment
    for word in words:
        assert word in comment, (name, word)


def test_measure_references_name_catalog_metrics_only() -> None:
    measures = load_dax()
    for name, measure in measures.items():
        refs = set(re.findall(r"(?<![\w\]'])\[([^\]@]+)\]", measure.expression))
        assert refs <= set(CATALOG.metrics), (name, refs)
        assert name not in refs, f"{name} refers to itself"
