"""Every figure in the agent's text must appear in the rows of a query it ran.

**What is a figure.** A number written in the text: ``$1,697,318,332.65``, ``6,016``,
``44.9%``, ``$1.70 billion``, ``$1.7BN``, ``USD 5,000``, ``2.5x``, ``-450``, both ends of
``10-15%``. A number that is part of a code or a word is not (``F108``, ``FY2025``, ``Q3``,
``5th``, ``7B20``, ``30MM``, a UEI), unless it is written with a currency sign: an amount is
always a figure, whatever follows it. Dates are checked as dates, written as ISO dates
(``2024-10-01``) or in words (``October 1, 2024``, ``1 October 2024``, ``September 30``).

**When a figure is grounded.** When it

- equals a **measured value**, a metric cell of a row, at the precision it is written, with the
  same sign: ``44.9%`` matches a share of 0.449020, ``$1.70 billion`` matches
  1,697,318,332.65, and a de-obligation keeps its minus; or
- is a plain whole number that **frames a query**: a grain or dimension value in the rows
  (fiscal year 2025, month 4, NAICS code 562910), a numeric filter value, the query's
  ``limit``, the ``n`` of a top-n metric, or the year of a query's time range or of the data's
  coverage; or
- is a **year the question wrote** (fiscal year 2023), so a refusal can repeat it. No other
  number of the question counts: "Were there 7,000 active awards?" cannot be answered "Yes,
  7,000" unless a query returned it.

A date is grounded when it is a date of a query's time range, of the coverage, or of the
question; a year range (``2024-25``) when both years frame a query. Nothing the model works
out itself passes: a sum or a difference of rows, a ratio, a multiple (``2.5x``, ``4 times``),
a growth rate, a count of rows or a rank written in digits. The agent runs a query for each
figure it needs and writes ranks and counts of rows as words. One limit stays: a computed
whole number that happens to equal a frame number (a count of 7 months after a query whose
rows hold fiscal month 7) cannot be told apart from it.

**Names.** Names come from the schema tool, never from rows, so a name grounds nothing. A name
the schema tool returned and the text writes out exactly as returned is text: a bare whole
number inside it (``CHENEGA SENTINEL 360, LLC``) is part of the name and is not checked. Any
other number inside it (with a currency sign, a decimal point, a percent, or a scale, glued or
spelled) is still a figure, and a name written in another case or without its legal suffix is
not the name, so a name cannot carry an amount into an answer, nor turn a sentence into a name.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from .answer import QueryRun

_ISO_DATE = re.compile(r"(?<![\w.\-/])\d{4}-\d{2}-\d{2}(?![\w\-])")
_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4,
    "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}  # fmt: skip
_MONTH = "(?:" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
_DAY = r"\d{1,2}(?:st|nd|rd|th)?"
_WRITTEN_DATE = re.compile(
    rf"\b(?P<month>{_MONTH})\s+(?P<day>{_DAY})(?:,?\s+(?P<year>\d{{4}}))?(?![\w,]\d)"
    rf"|\b(?P<day2>{_DAY})\s+(?P<month2>{_MONTH})(?:,?\s+(?P<year2>\d{{4}}))?\b",
    re.IGNORECASE,
)
# fiscal year 2024-25 (both years must frame a query), or a year and a month (2024-10)
_YEAR_RANGE = re.compile(r"(?<![\w.\-/])(?P<first>\d{4})[-/](?P<second>\d{2})(?![\w\-/])")
_NUMBER = re.compile(
    r"""
    (?<![\w.])                      # not inside a word, a code or a decimal
    (?<![A-Za-z_][-\u2212/,])       # not joined to a word: top-5, COVID-19
    (?P<sign>[-\u2212])?
    (?P<currency>\$|US\$|USD\s?)?
    (?P<sign2>[-\u2212])?
    (?P<digits>(?>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?))  # atomic: never "2" of "2.5x"
    (?P<glued>[\w%\u00d7]*)         # glued on: 44.9%, 1.7bn, 2.5x, 5th, 7B20, 30MM
    (?:
        \s(?P<word>(?i:percent|per\s+cent|thousand|million|billion|trillion|bn|mn|tn))\b
      | \s(?P<letter>[kKmMbB])\b
      | \s?(?P<spaced>%|\u00d7)
      | (?:\s|-)(?P<times>(?i:times|fold))\b
    )?
    """,
    re.VERBOSE,
)
_HUNDREDTH = Decimal("0.01")
_SCALES = {
    "%": _HUNDREDTH,
    "percent": _HUNDREDTH,
    "per cent": _HUNDREDTH,
    "k": Decimal(10) ** 3,
    "thousand": Decimal(10) ** 3,
    "m": Decimal(10) ** 6,
    "mn": Decimal(10) ** 6,
    "million": Decimal(10) ** 6,
    "b": Decimal(10) ** 9,
    "bn": Decimal(10) ** 9,
    "billion": Decimal(10) ** 9,
    "tn": Decimal(10) ** 12,
    "trillion": Decimal(10) ** 12,
}
# "mm" is left out on purpose: in product names it means millimetres (GUNS, THROUGH 30MM).
_MULTIPLIERS = ("x", "\u00d7")
_NUMERIC_TEXT = re.compile(r"-?\d+(?:\.\d+)?")
# a year of the question: 19xx or 20xx right after a word that names a time ("fiscal year 2023",
# "FY2023", "in 2023"), so that "Were 2000 awards active?" grounds nothing
_QUESTION_YEAR = re.compile(
    r"(?i:\b(?:fy|year|in|since|for|during|until|through|before|after|of)\s*)"
    r"(?P<year>(?:19|20)\d{2})\b"
)
# A name worth masking: at least four characters with a letter and a digit. Pure digits stay
# figures or frame numbers; a name without a digit holds no number to mask.
_NAME = re.compile(r"(?=.*[A-Za-z])(?=.*\d).{4,}", re.DOTALL)


@dataclass(frozen=True)
class Figure:
    """One number as written, with the value and rounding it stands for."""

    text: str
    value: Decimal
    tolerance: Decimal
    plain_integer: bool
    # where it was found, and whether it may be part of a name (a whole number, bare or
    # glued to letters, with no currency, decimal point, percent or scale word)
    start: int = field(default=0, compare=False)
    end: int = field(default=0, compare=False)
    name_like: bool = field(default=False, compare=False)


@dataclass(frozen=True)
class Evidence:
    measured: tuple[Decimal, ...]
    frame: frozenset[Decimal]
    stated: frozenset[Decimal]
    dates: frozenset[str]
    # names and codes the schema tool returned that hold a letter and a digit (see _NAME)
    names: tuple[str, ...] = ()


def find_figures(text: str) -> tuple[list[Figure], list[str]]:
    """The figures in ``text`` and the dates in it, each in order of appearance."""
    dates, masked = _dates(text)
    return _figures(masked), [written for written, _ in dates]


def build_evidence(
    runs: Sequence[QueryRun],
    *,
    coverage: Sequence[dt.date] = (),
    top_n: Iterable[int] = (),
    question: str = "",
    names: Iterable[str] = (),
) -> Evidence:
    """What figures may match: the runs' metric cells, the numbers and dates that frame the
    queries, the plain whole numbers and dates of the question, and the schema tool's names."""
    measured: list[Decimal] = []
    frame: set[Decimal] = {Decimal(n) for n in top_n}
    days: set[dt.date] = set(coverage)
    for run in runs:
        for row in run.rows:
            *keys, value = row
            number = _number(value)
            if number is not None:
                measured.append(number)
            frame.update(n for n in map(_frame_number, keys) if n is not None)
        for item in run.query.get("filters", []):
            values = item["value"] if isinstance(item["value"], list) else [item["value"]]
            frame.update(n for n in map(_frame_number, values) if n is not None)
        if run.query.get("limit") is not None:
            frame.add(Decimal(run.query["limit"]))
        time_range = run.query.get("time_range")
        if time_range:
            days.update(dt.date.fromisoformat(time_range[key]) for key in ("start", "end"))
    frame.update(Decimal(day.year) for day in days)
    question_dates, masked = _dates(question)
    stated = {Decimal(match.group("year")) for match in _QUESTION_YEAR.finditer(masked)}
    allowed = {day.isoformat() for day in days}
    allowed |= {when.isoformat() for _, when in question_dates if isinstance(when, dt.date)}
    return Evidence(
        measured=tuple(measured),
        frame=frozenset(frame),
        stated=frozenset(stated),
        dates=frozenset(allowed),
        names=tuple(sorted({n for n in names if _NAME.fullmatch(n)}, key=len, reverse=True)),
    )


def ungrounded(text: str, evidence: Evidence) -> list[str]:
    """Each figure or date of ``text`` that matches nothing in ``evidence``, as written."""
    dates, masked = _dates(text)
    allowed = {dt.date.fromisoformat(day) for day in evidence.dates}
    month_days = {(day.month, day.day) for day in allowed}
    missing = [
        written
        for written, when in dates
        if not (when in allowed if isinstance(when, dt.date) else when in month_days)
    ]
    years = evidence.frame | evidence.stated
    for match in _YEAR_RANGE.finditer(masked):
        first, tail = int(match.group("first")), int(match.group("second"))
        second = first // 100 * 100 + tail
        span_of_years = second == first + 1 and {Decimal(first), Decimal(second)} <= years
        year_and_month = 1 <= tail <= 12 and Decimal(first) in years  # 2024-10
        if not (span_of_years or year_and_month):
            missing.append(match.group(0))
    masked = _YEAR_RANGE.sub(lambda match: " " * len(match.group(0)), masked)
    spans = _name_spans(masked, evidence.names)
    for figure in _figures(masked):
        if figure.name_like and any(a <= figure.start and figure.end <= b for a, b in spans):
            continue  # a whole number inside a name the schema tool returned
        if any(abs(value - figure.value) <= figure.tolerance for value in evidence.measured):
            continue
        if figure.plain_integer and (
            figure.value in evidence.frame or figure.value in evidence.stated
        ):
            continue
        missing.append(figure.text)
    return missing


def _figures(text: str) -> list[Figure]:
    figures = []
    for match in _NUMBER.finditer(text):
        currency = match.group("currency") is not None
        glued = match.group("glued")
        scale, multiplier = Decimal(1), False
        if glued:
            key = glued.lower()
            if key in _SCALES:
                scale = _SCALES[key]
            elif key in _MULTIPLIERS:
                multiplier = True
            elif not currency and match.group("digits").isdigit():
                continue  # a bare digit run glued to a word or a code (5th, 7B20, 30MM)
        elif match.group("word"):
            scale = _SCALES[" ".join(match.group("word").lower().split())]
        elif match.group("letter") and currency:
            scale = _SCALES[match.group("letter").lower()]
        elif match.group("spaced"):
            if match.group("spaced") in _MULTIPLIERS:
                multiplier = True
            else:
                scale = _HUNDREDTH
        elif match.group("times"):
            multiplier = True
        digits = match.group("digits").replace(",", "")
        decimals = len(digits.split(".", 1)[1]) if "." in digits else 0
        negative = bool(match.group("sign") or match.group("sign2"))
        value = Decimal(digits) * scale * (-1 if negative else 1)
        tolerance = Decimal(5) * Decimal(10) ** (-decimals - 1) * scale
        plain = not currency and decimals == 0 and not multiplier and scale == 1 and not glued
        name_like = plain  # only a bare whole number may be part of a name
        written = match.group(0)
        start = match.start() + len(written) - len(written.lstrip())
        figures.append(
            Figure(written.strip(), value, tolerance, plain, start, match.end(), name_like)
        )
    return figures


def _dates(text: str) -> tuple[list[tuple[str, dt.date | tuple[int, int] | None]], str]:
    """The dates written in ``text`` with what each means, and the text with them blanked.

    A full date means that day; a month and a day without a year mean that pair; a date that
    is not a calendar day means nothing and is never grounded.
    """
    found: list[tuple[int, str, dt.date | tuple[int, int] | None]] = []
    for match in _ISO_DATE.finditer(text):
        try:
            found.append((match.start(), match.group(0), dt.date.fromisoformat(match.group(0))))
        except ValueError:
            found.append((match.start(), match.group(0), None))
    masked = _ISO_DATE.sub(lambda match: " " * len(match.group(0)), text)
    spans = []
    for match in _WRITTEN_DATE.finditer(masked):
        word = match.group("month") or match.group("month2")
        year = match.group("year") or match.group("year2")
        if not year and (not word[0].isupper() or word.lower().startswith("may")):
            continue  # without a year, "5 may change" and "5 march ahead" are not dates
        month = _MONTHS[word.lower().rstrip(".")]
        day = int(re.match(r"\d+", match.group("day") or match.group("day2")).group(0))
        when: dt.date | tuple[int, int] | None
        try:
            dt.date(int(year) if year else 2000, month, day)  # 2000 is a leap year
            when = dt.date(int(year), month, day) if year else (month, day)
        except ValueError:
            when = None
        found.append((match.start(), match.group(0), when))
        spans.append(match.span())
    for start, end in spans:
        masked = masked[:start] + " " * (end - start) + masked[end:]
    found.sort(key=lambda item: item[0])
    return [(written, when) for _, written, when in found], masked


def _name_spans(text: str, names: Sequence[str]) -> list[tuple[int, int]]:
    """Where the names occur in ``text``, longest names first, never overlapping."""
    spans: list[tuple[int, int]] = []
    for name in names:
        pattern = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)")
        for match in pattern.finditer(text):
            if all(match.end() <= a or b <= match.start() for a, b in spans):
                spans.append((match.start(), match.end()))
    return spans


def _number(value: object) -> Decimal | None:
    """A measured value as an exact decimal; None for nulls and anything not a number."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
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
