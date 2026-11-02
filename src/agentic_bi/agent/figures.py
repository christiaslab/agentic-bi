"""Every figure in the agent's text must appear in the rows of a query it ran.

A figure is a number written in the text: ``$1,697,318,332.65``, ``6,016``, ``44.9%``,
``1.70 billion``, ``-450``. Numbers that are part of a word or a code (``F108``, ``FY2025``,
``Q3``, ``5th``, a UEI) are not figures, and ISO dates are checked as dates.

A figure is grounded when either

- it equals a **measured value**, a metric cell of a row, at the precision it is written:
  ``44.9%`` matches a share of 0.449020 and ``1.70 billion`` matches 1,697,318,332.65, but the
  sign must agree, so a de-obligation keeps its minus; or
- it is a plain whole number (no currency, decimals, percent or scale word) equal to a
  **frame number** of the queries: a grain or dimension value in the rows (fiscal year 2025,
  month 4, NAICS code 562910), a numeric filter value, the query's ``limit``, the ``n`` of a
  top-n metric, a rank from 1 to the number of rows, or a year, month or day of the time range
  or of the data's coverage; or
- it has the value of a figure the user wrote in the question ("over 2 billion", "fiscal
  year 2023"), so that an answer or a refusal can repeat it.

Label text never counts: names come from the schema tool, not from rows, so a recipient name
that holds a number cannot ground it. Nothing the model computes itself (a sum of rows, a
difference, a growth rate) is grounded; the agent must run a query for it instead.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from .answer import QueryRun

_ISO_DATE = re.compile(r"(?<![\w.\-/])\d{4}-\d{2}-\d{2}(?![\w\-])")
_NUMBER = re.compile(
    r"""
    (?<![\w.,\-−/])
    (?P<sign>[-−])?
    \$?
    (?P<sign2>[-−])?
    (?P<digits>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)
    (?P<suffix>
        \s?(?:%|percent\b|per\ cent\b)
      | \s(?:thousand|million|billion|trillion)\b
      | (?:k|K|m|M|bn|B)\b
    )?
    (?!\w)
    """,
    re.VERBOSE,
)
_SCALES = {
    "%": Decimal("0.01"),
    "percent": Decimal("0.01"),
    "per cent": Decimal("0.01"),
    "thousand": Decimal(10) ** 3,
    "k": Decimal(10) ** 3,
    "million": Decimal(10) ** 6,
    "m": Decimal(10) ** 6,
    "billion": Decimal(10) ** 9,
    "bn": Decimal(10) ** 9,
    "b": Decimal(10) ** 9,
    "trillion": Decimal(10) ** 12,
}
_NUMERIC_TEXT = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Figure:
    """One number as written, with the value and rounding it stands for."""

    text: str
    value: Decimal
    tolerance: Decimal
    plain_integer: bool


@dataclass(frozen=True)
class Evidence:
    measured: tuple[Decimal, ...]
    frame: frozenset[Decimal]
    stated: frozenset[Decimal]
    dates: frozenset[str]


def find_figures(text: str) -> tuple[list[Figure], list[str]]:
    """The figures in ``text`` and the ISO dates in it, in order of appearance."""
    dates = [match.group(0) for match in _ISO_DATE.finditer(text)]
    masked = _ISO_DATE.sub(lambda match: " " * len(match.group(0)), text)
    figures = []
    for match in _NUMBER.finditer(masked):
        digits = match.group("digits").replace(",", "")
        decimals = len(digits.split(".", 1)[1]) if "." in digits else 0
        suffix = (match.group("suffix") or "").strip()
        scale = _SCALES[suffix.lower()] if suffix else Decimal(1)
        negative = bool(match.group("sign") or match.group("sign2"))
        value = Decimal(digits) * scale * (-1 if negative else 1)
        tolerance = Decimal(5) * Decimal(10) ** (-decimals - 1) * scale
        plain = not suffix and decimals == 0 and "$" not in match.group(0)
        figures.append(Figure(match.group(0).strip(), value, tolerance, plain))
    return figures, dates


def build_evidence(
    runs: Sequence[QueryRun],
    *,
    coverage: Sequence[dt.date] = (),
    top_n: Iterable[int] = (),
    question: str = "",
) -> Evidence:
    """What figures may match: the runs' metric cells, their frame numbers and dates, and the
    figures and dates of the question."""
    measured: list[Decimal] = []
    frame: set[Decimal] = {Decimal(n) for n in top_n}
    days: set[dt.date] = set(coverage)
    stated_figures, stated_dates = find_figures(question)
    for run in runs:
        for row in run.rows:
            *keys, value = row
            number = _number(value)
            if number is not None:
                measured.append(number)
            frame.update(n for n in map(_frame_number, keys) if n is not None)
        frame.update(Decimal(rank) for rank in range(1, len(run.rows) + 1))
        for item in run.query.get("filters", []):
            values = item["value"] if isinstance(item["value"], list) else [item["value"]]
            frame.update(n for n in map(_frame_number, values) if n is not None)
        if run.query.get("limit") is not None:
            frame.add(Decimal(run.query["limit"]))
        time_range = run.query.get("time_range")
        if time_range:
            days.update(dt.date.fromisoformat(time_range[key]) for key in ("start", "end"))
    for day in days:
        frame.update(Decimal(part) for part in (day.year, day.month, day.day))
    return Evidence(
        measured=tuple(measured),
        frame=frozenset(frame),
        stated=frozenset(figure.value for figure in stated_figures),
        dates=frozenset(day.isoformat() for day in days) | frozenset(stated_dates),
    )


def ungrounded(text: str, evidence: Evidence) -> list[str]:
    """Each figure or date of ``text`` that matches nothing in ``evidence``, as written."""
    figures, dates = find_figures(text)
    missing = [date for date in dates if date not in evidence.dates]
    for figure in figures:
        if any(abs(value - figure.value) <= figure.tolerance for value in evidence.measured):
            continue
        if figure.plain_integer and figure.value in evidence.frame:
            continue
        if figure.value in evidence.stated:
            continue
        missing.append(figure.text)
    return missing


def _number(value: object) -> Decimal | None:
    """A measured value as an exact decimal; None for nulls and anything not a number."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # nan and the infinities are not figures; repr gives the shortest exact decimal
        return Decimal(repr(value)) if math.isfinite(value) else None
    return None


def _frame_number(value: object) -> Decimal | None:
    """A whole number that frames a query: an int cell or a code written in digits."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str) and _NUMERIC_TEXT.fullmatch(value):
        number = Decimal(value)
        return number if number == number.to_integral_value() else None
    return None
