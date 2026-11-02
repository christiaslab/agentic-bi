"""Tests for the comparison rules of agentic_bi.reproduce.compare: exact amounts and counts,
and half a unit of the last written place for ratios."""

from __future__ import annotations

from decimal import Decimal

import pytest

from agentic_bi.reproduce.compare import (
    EXACT,
    HALF_UNIT,
    ComparisonError,
    compare,
    tolerance_for,
)

RATIO_KINDS = ["ratio", "top_n_share", "share_where"]


def test_reproduce_sum_equal_decimal_matches_with_zero_difference():
    result = compare("sum", "1234.50", Decimal("1234.50"))

    assert result.matches is True
    assert result.difference == Decimal("0.00")
    assert result.golden == Decimal("1234.50")
    assert result.reproduced == Decimal("1234.50")
    assert result.rule == EXACT
    assert result.tolerance is None


def test_reproduce_sum_equal_value_with_other_scale_matches():
    assert compare("sum", "10.00", Decimal("10")).matches is True


@pytest.mark.parametrize(
    ("reproduced", "difference"),
    [(Decimal("1234.51"), Decimal("0.01")), (Decimal("1234.49"), Decimal("-0.01"))],
)
def test_reproduce_sum_one_cent_off_differs_with_signed_difference(reproduced, difference):
    result = compare("sum", "1234.50", reproduced)

    assert result.matches is False
    assert result.difference == difference


def test_reproduce_sum_difference_is_reproduced_minus_golden():
    assert compare("sum", "100.00", Decimal("90.00")).difference == Decimal("-10.00")


def test_reproduce_sum_negative_golden_is_compared_exactly():
    assert compare("sum", "-5.25", Decimal("-5.25")).matches is True


def test_reproduce_sum_reproduced_none_differs_without_difference():
    result = compare("sum", "1234.50", None)

    assert result.matches is False
    assert result.difference is None
    assert result.reproduced is None
    assert result.golden == Decimal("1234.50")
    assert result.rule == EXACT


@pytest.mark.parametrize("reproduced", [1234.5, 12, "1234.50"], ids=["float", "int", "str"])
def test_reproduce_sum_reproduced_not_a_decimal_raises(reproduced):
    with pytest.raises(ComparisonError, match="exact decimal"):
        compare("sum", "1234.50", reproduced)


@pytest.mark.parametrize("golden", [1234, 0, True], ids=["int", "zero", "bool"])
def test_reproduce_sum_golden_that_is_not_a_string_raises(golden):
    with pytest.raises(ComparisonError, match="decimal string"):
        compare("sum", golden, Decimal("1234"))


@pytest.mark.parametrize("golden", ["abc", "", "1,234.50", "NaN", "Infinity", "-Infinity"])
def test_reproduce_sum_golden_that_is_not_a_finite_decimal_raises(golden):
    with pytest.raises(ComparisonError, match="decimal"):
        compare("sum", golden, Decimal("1"))


def test_reproduce_sum_invalid_golden_raises_even_when_nothing_was_reproduced():
    with pytest.raises(ComparisonError):
        compare("sum", "abc", None)


def test_reproduce_count_distinct_equal_ints_match():
    result = compare("count_distinct", 12687, 12687)

    assert result.matches is True
    assert result.difference == 0
    assert result.rule == EXACT
    assert result.golden == 12687
    assert result.reproduced == 12687


@pytest.mark.parametrize(("reproduced", "difference"), [(12688, 1), (12686, -1)])
def test_reproduce_count_distinct_off_by_one_differs(reproduced, difference):
    result = compare("count_distinct", 12687, reproduced)

    assert result.matches is False
    assert result.difference == difference


def test_reproduce_count_distinct_reproduced_none_differs_without_difference():
    result = compare("count_distinct", 5, None)

    assert result.matches is False
    assert result.difference is None
    assert result.reproduced is None


@pytest.mark.parametrize(
    "reproduced", [True, Decimal("5"), 5.0, "5"], ids=["bool", "decimal", "float", "str"]
)
def test_reproduce_count_distinct_reproduced_not_an_int_raises(reproduced):
    with pytest.raises(ComparisonError, match="integer"):
        compare("count_distinct", 5, reproduced)


@pytest.mark.parametrize("golden", ["5", True, 5.0], ids=["str", "bool", "float"])
def test_reproduce_count_distinct_golden_that_is_not_an_int_raises(golden):
    with pytest.raises(ComparisonError, match="golden count is an integer"):
        compare("count_distinct", golden, 5)


@pytest.mark.parametrize("kind", RATIO_KINDS)
def test_reproduce_ratio_real_share_matches(kind):
    result = compare(kind, "0.449020", 0.4490202395623079)

    assert result.matches is True
    assert result.rule == HALF_UNIT
    assert result.tolerance == Decimal("0.0000005")
    assert result.golden == Decimal("0.449020")
    assert result.reproduced == Decimal("0.4490202395623079")
    assert result.difference == Decimal("0.0000002395623079")


@pytest.mark.parametrize("kind", RATIO_KINDS)
def test_reproduce_ratio_real_average_award_value_matches(kind):
    result = compare(kind, "282134.03", 282134.03135804523)

    assert result.matches is True
    assert result.tolerance == Decimal("0.005")


def test_reproduce_tolerance_is_half_a_unit_of_the_last_written_place():
    assert tolerance_for("0.449020") == Decimal("0.0000005")
    assert tolerance_for("282134.03") == Decimal("0.005")


@pytest.mark.parametrize(
    ("golden", "tolerance"),
    [
        ("0.45", Decimal("0.005")),
        ("0.450000", Decimal("0.0000005")),
        ("12", Decimal("0.5")),
        ("1E+2", Decimal("50")),
    ],
)
def test_reproduce_tolerance_counts_trailing_zeros(golden, tolerance):
    assert tolerance_for(golden) == tolerance


def test_reproduce_trailing_zeros_change_the_verdict():
    reproduced = Decimal("0.4549")

    assert compare("ratio", "0.45", reproduced).matches is True
    assert compare("ratio", "0.450000", reproduced).matches is False


@pytest.mark.parametrize("golden", ["abc", "NaN", "Infinity", ""])
def test_reproduce_tolerance_for_non_finite_golden_raises(golden):
    with pytest.raises(ComparisonError):
        tolerance_for(golden)


@pytest.mark.parametrize("kind", RATIO_KINDS)
@pytest.mark.parametrize(
    ("reproduced", "matches"),
    [
        (Decimal("0.4490205"), True),
        (Decimal("0.4490195"), True),
        (Decimal("0.44902050001"), False),
        (Decimal("0.44901949999"), False),
        (Decimal("0.449020"), True),
    ],
)
def test_reproduce_ratio_half_unit_boundary_is_inclusive(kind, reproduced, matches):
    result = compare(kind, "0.449020", reproduced)

    assert result.matches is matches
    assert result.difference == reproduced - Decimal("0.449020")


@pytest.mark.parametrize(
    ("reproduced", "matches"),
    [
        (Decimal("282134.035"), True),
        (Decimal("282134.025"), True),
        (Decimal("282134.03500001"), False),
        (Decimal("282134.02499999"), False),
    ],
)
def test_reproduce_ratio_boundary_with_two_places(reproduced, matches):
    assert compare("ratio", "282134.03", reproduced).matches is matches


@pytest.mark.parametrize("reproduced", [float("nan"), float("inf"), float("-inf")])
def test_reproduce_ratio_non_finite_float_differs_instead_of_raising(reproduced):
    result = compare("ratio", "0.449020", reproduced)

    assert result.matches is False
    assert result.reproduced is None
    assert result.difference is None
    assert result.rule == HALF_UNIT
    assert result.tolerance == Decimal("0.0000005")


@pytest.mark.parametrize("reproduced", [Decimal("NaN"), Decimal("Infinity")])
def test_reproduce_ratio_non_finite_decimal_differs_instead_of_raising(reproduced):
    result = compare("share_where", "0.449020", reproduced)

    assert result.matches is False
    assert result.difference is None


def test_reproduce_ratio_reproduced_none_differs_with_the_tolerance():
    result = compare("top_n_share", "0.449020", None)

    assert result.matches is False
    assert result.reproduced is None
    assert result.difference is None
    assert result.tolerance == Decimal("0.0000005")


@pytest.mark.parametrize("reproduced", ["0.449020", True, [0.45]], ids=["str", "bool", "list"])
def test_reproduce_ratio_reproduced_of_other_type_raises(reproduced):
    with pytest.raises(ComparisonError, match="must be a number"):
        compare("ratio", "0.449020", reproduced)


@pytest.mark.parametrize("golden", [3, True], ids=["int", "bool"])
def test_reproduce_ratio_golden_that_is_not_a_string_raises(golden):
    with pytest.raises(ComparisonError, match="decimal string"):
        compare("ratio", golden, 0.5)


@pytest.mark.parametrize("golden", ["abc", "NaN", "Infinity"])
def test_reproduce_ratio_golden_that_is_not_a_finite_decimal_raises(golden):
    with pytest.raises(ComparisonError):
        compare("ratio", golden, 0.5)


def test_reproduce_ratio_float_is_read_as_its_shortest_decimal():
    result = compare("ratio", "0.10", 0.1)

    assert result.reproduced == Decimal("0.1")
    assert result.matches is True


@pytest.mark.parametrize("kind", ["median", "", "SUM", "avg"])
def test_reproduce_unknown_kind_raises(kind):
    with pytest.raises(ComparisonError, match="no comparison rule"):
        compare(kind, "1.00", Decimal("1.00"))


@pytest.mark.parametrize(
    ("kind", "golden", "reproduced", "rule"),
    [
        ("sum", "1.00", Decimal("1.00"), "exact"),
        ("count_distinct", 3, 3, "exact"),
        ("ratio", "0.50", 0.5, "half_unit_of_last_place"),
        ("top_n_share", "0.50", 0.5, "half_unit_of_last_place"),
        ("share_where", "0.50", 0.5, "half_unit_of_last_place"),
    ],
)
def test_reproduce_rule_name_follows_the_metric_kind(kind, golden, reproduced, rule):
    assert compare(kind, golden, reproduced).rule == rule
