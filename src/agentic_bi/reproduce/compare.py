"""How a reproduced value is compared with its golden value (docs/reproduction.md).

The rule follows the metric's kind in the catalog, never a setting of the mapping, so no
entry can loosen its own comparison.

- ``sum`` (amounts): the compiler returns an exact DECIMAL; it must equal the golden decimal
  string to the cent, with no tolerance.
- ``count_distinct`` (counts): an integer equal to the golden integer.
- ``ratio``, ``top_n_share``, ``share_where`` (ratios): the compiler returns a DOUBLE and the
  golden file stores the exact quotient rounded half-even to the places it is written with
  (six for shares, two for the average award value). The value reproduces when it lies
  within half a unit of that last place, inclusive: exactly when rounding it to those places
  can give the golden value.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

EXACT = "exact"
HALF_UNIT = "half_unit_of_last_place"

AMOUNT_KINDS = ("sum",)
COUNT_KINDS = ("count_distinct",)
RATIO_KINDS = ("ratio", "top_n_share", "share_where")


class ComparisonError(TypeError):
    """A golden or reproduced value has a type its metric kind cannot have."""


@dataclass(frozen=True)
class Comparison:
    rule: str
    matches: bool
    golden: Decimal | int
    reproduced: Decimal | int | None
    # reproduced minus golden; None when nothing was reproduced
    difference: Decimal | int | None
    tolerance: Decimal | None = None


def compare(kind: str, golden: str | int, reproduced: Any) -> Comparison:
    """Compare a reproduced value with its golden value under the rule of the metric kind."""
    if kind in AMOUNT_KINDS:
        return _compare_amount(golden, reproduced)
    if kind in COUNT_KINDS:
        return _compare_count(golden, reproduced)
    if kind in RATIO_KINDS:
        return _compare_ratio(golden, reproduced)
    raise ComparisonError(f"no comparison rule for metric kind {kind!r}")


def tolerance_for(golden: str) -> Decimal:
    """Half a unit of the golden value's last written decimal place."""
    exponent = _decimal(golden, "golden").as_tuple().exponent
    if not isinstance(exponent, int):
        raise ComparisonError(f"golden value {golden!r} is not a finite decimal")
    return Decimal(5).scaleb(exponent - 1)


def _compare_amount(golden: str | int, reproduced: Any) -> Comparison:
    if not isinstance(golden, str):
        raise ComparisonError("a golden amount is a decimal string")
    expected = _decimal(golden, "golden")
    if reproduced is None:
        return Comparison(EXACT, False, expected, None, None)
    if not isinstance(reproduced, Decimal):
        # a float amount would already have lost the cent; refuse it rather than round it
        raise ComparisonError(
            f"a reproduced amount must be an exact decimal, not {type(reproduced).__name__}"
        )
    difference = reproduced - expected
    return Comparison(EXACT, difference == 0, expected, reproduced, difference)


def _compare_count(golden: str | int, reproduced: Any) -> Comparison:
    if type(golden) is not int:
        raise ComparisonError("a golden count is an integer")
    if reproduced is None:
        return Comparison(EXACT, False, golden, None, None)
    if type(reproduced) is not int:
        raise ComparisonError(
            f"a reproduced count must be an integer, not {type(reproduced).__name__}"
        )
    return Comparison(EXACT, reproduced == golden, golden, reproduced, reproduced - golden)


def _compare_ratio(golden: str | int, reproduced: Any) -> Comparison:
    if not isinstance(golden, str):
        raise ComparisonError("a golden ratio is a decimal string")
    expected = _decimal(golden, "golden")
    tolerance = tolerance_for(golden)
    if reproduced is None:
        return Comparison(HALF_UNIT, False, expected, None, None, tolerance)
    if isinstance(reproduced, float):
        if not math.isfinite(reproduced):
            return Comparison(HALF_UNIT, False, expected, None, None, tolerance)
        # the shortest decimal that reads back as the same double
        value = Decimal(repr(reproduced))
    elif isinstance(reproduced, Decimal):
        if not reproduced.is_finite():
            return Comparison(HALF_UNIT, False, expected, None, None, tolerance)
        value = reproduced
    else:
        raise ComparisonError(
            f"a reproduced ratio must be a number, not {type(reproduced).__name__}"
        )
    difference = value - expected
    matches = abs(difference) <= tolerance
    return Comparison(HALF_UNIT, matches, expected, value, difference, tolerance)


def _decimal(text: str, what: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise ComparisonError(f"{what} value {text!r} is not a decimal") from exc
    if not value.is_finite():
        raise ComparisonError(f"{what} value {text!r} is not a finite decimal")
    return value
