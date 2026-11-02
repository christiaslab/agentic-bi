"""The figure check: every figure in the agent's text must appear in the rows of a query it ran.

The queries are built by hand, the way the tool layer records them: no database, no model.
Every value is a golden value of docs/golden/golden.json, or a sum of golden values where the
comment says so, so no figure here is estimated. The unicode minus sign U+2212 is written as an
escape, so that it cannot be mistaken for a dash.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from typing import Any

import pytest

from agentic_bi.agent.answer import QueryRun
from agentic_bi.agent.figures import Evidence, Figure, build_evidence, find_figures, ungrounded

MINUS = "−"  # the unicode minus sign: not a hyphen, not a dash
FY = {"start": "2024-10-01", "end": "2025-09-30"}
COVERAGE = (dt.date(2024, 10, 1), dt.date(2025, 9, 30))


def make_run(
    rows: Iterable[tuple[Any, ...]], columns: tuple[str, ...] = ("obligations",), **query: Any
) -> QueryRun:
    """A query that ran, as the tool layer records it, with a stand-in SQL text."""
    structured: dict[str, Any] = {
        "metric": columns[-1],
        "dimensions": [],
        "filters": [],
        "grain": "total",
        "time_range": None,
    }
    structured.update(query)
    return QueryRun(
        query=structured, sql="SELECT 1", params=(), columns=tuple(columns), rows=tuple(rows)
    )


def read(text: str) -> list[Figure]:
    return find_figures(text)[0]


def missing(text: str, *runs: QueryRun, **evidence: Any) -> list[str]:
    """The figures and dates of ``text`` that the evidence of ``runs`` does not hold."""
    return ungrounded(text, build_evidence(runs, **evidence))


RECIPIENT_COLUMNS = ("recipient", "obligations")
RECIPIENTS = (
    ("DBD6KDKXWN65", Decimal("141735682.10")),
    ("VHPJR3MJWV49", Decimal("136907984.75")),
    ("MHMPN3F73NX3", Decimal("115370521.15")),
    ("Z4XGALW7L9K6", Decimal("84868589.24")),
    ("DK4TBEJRAR56", Decimal("66893110.69")),
)
TOP3 = make_run(RECIPIENTS[:3], RECIPIENT_COLUMNS, dimensions=["recipient"])
TOP5 = make_run(
    RECIPIENTS,
    RECIPIENT_COLUMNS,
    dimensions=["recipient"],
    time_range=FY,
    order_by={"field": "obligations", "direction": "desc"},
    limit=5,
)
# The runs below have no time range, so a year, month or day can reach the evidence only through
# their rows or through the settings a test passes.
TOTAL = make_run([(Decimal("1697318332.65"),)])
TOTAL_FY = make_run([(Decimal("1697318332.65"),)], time_range=FY)
COUNT = make_run([(6016,)], ("award_count",))
AVERAGE = make_run([(282134.03135804523,)], ("avg_award_value",))
SHARE = make_run([(0.4490202395623079,)], ("supplier_concentration_top10",))
RATE = make_run([(0.9298753995520864,)], ("competition_rate",))
# The second quarter of fiscal year 2025 is fiscal months 4, 5 and 6; the cell adds them up.
QUARTER = make_run(
    [(Decimal("530017185.75"),)], time_range={"start": "2025-01-01", "end": "2025-03-31"}
)
# Fiscal months 4 and 5 of fiscal year 2025: the year and the month are grain cells.
MONTHS = make_run(
    [(2025, 4, Decimal("311975420.85")), (2025, 5, Decimal("151198107.99"))],
    ("fiscal_year", "fiscal_month", "obligations"),
    grain="fiscal_month",
)
NAICS = make_run(
    [("562910", Decimal("768867751.92")), ("541620", Decimal("349784383.93"))],
    ("naics", "obligations"),
    dimensions=["naics"],
)
PSC = make_run(
    [("F108", Decimal("716008251.57")), ("R499", Decimal("226716569.88"))],
    ("psc", "obligations"),
    dimensions=["psc"],
)
# Obligations by the competed flag. Competed is true for the codes A, D and F, false for B, C and
# G, and null when the transaction has no code. The cells are the golden extent-competed values
# added up that way, and they add up to the golden total. The null group is net negative.
BY_COMPETED = make_run(
    [
        (True, Decimal("1578294562.74")),
        (False, Decimal("119071157.35")),
        (None, Decimal("-47387.44")),
    ],
    ("competed", "obligations"),
    dimensions=["competed"],
)

# --- reading figures from text ----------------------------------------------------------------

READ = [
    pytest.param("$1,697,318,332.65", "1697318332.65", "0.005", False, id="dollars-and-cents"),
    pytest.param("$1,697,318,333", "1697318333", "0.5", False, id="whole-dollars"),
    pytest.param("1,697,318,332.65", "1697318332.65", "0.005", False, id="amount-no-dollar-sign"),
    pytest.param("6,016", "6016", "0.5", True, id="count-with-separator"),
    pytest.param("6016", "6016", "0.5", True, id="count-without-separator"),
    pytest.param("282134.03", "282134.03", "0.005", False, id="decimal-amount"),
    pytest.param("44.9%", "0.449", "0.0005", False, id="percent-sign"),
    pytest.param("44.9 %", "0.449", "0.0005", False, id="percent-sign-after-a-space"),
    pytest.param("44.9 percent", "0.449", "0.0005", False, id="percent-word"),
    pytest.param("44.9 per cent", "0.449", "0.0005", False, id="per-cent-words"),
    pytest.param("45%", "0.45", "0.005", False, id="whole-percent"),
    pytest.param("$1.70 billion", "1700000000", "5000000", False, id="billion-word"),
    pytest.param("1.7bn", "1700000000", "50000000", False, id="bn-suffix"),
    pytest.param("$141.7M", "141700000", "50000", False, id="m-suffix"),
    pytest.param("282k", "282000", "500", False, id="k-suffix"),
    pytest.param("12 thousand", "12000", "500", False, id="thousand-word"),
    pytest.param("3 million", "3000000", "500000", False, id="million-word"),
    pytest.param("2 trillion", "2000000000000", "500000000000", False, id="trillion-word"),
]


@pytest.mark.parametrize(("text", "value", "tolerance", "plain"), READ)
def test_a_figure_is_read_with_its_value_and_the_rounding_it_is_written_at(
    text: str, value: str, tolerance: str, plain: bool
) -> None:
    # the tolerance is half a unit of the last digit written, in the unit of the scale word
    (figure,) = read(f"It came to {text} overall.")
    assert figure.text == text
    assert figure.value == Decimal(value)
    assert figure.tolerance == Decimal(tolerance)
    assert figure.plain_integer is plain


NEGATIVE = [
    pytest.param("-$450.00", id="hyphen-then-dollar"),
    pytest.param("$-450.00", id="dollar-then-hyphen"),
    pytest.param(f"{MINUS}$450.00", id="unicode-minus-then-dollar"),
    pytest.param(f"${MINUS}450.00", id="dollar-then-unicode-minus"),
    pytest.param("-450.00", id="hyphen"),
    pytest.param(f"{MINUS}450.00", id="unicode-minus"),
]


@pytest.mark.parametrize("text", NEGATIVE)
def test_a_minus_sign_in_front_of_a_number_makes_the_figure_negative(text: str) -> None:
    (figure,) = read(f"The net change was {text} for the month.")
    assert figure.text == text
    assert figure.value == Decimal("-450.00")


def test_a_hyphen_after_a_number_is_not_a_minus_sign() -> None:
    (figure,) = read("It is a 5-year term.")
    assert figure.value == Decimal(5)


NOT_FIGURES = [
    pytest.param("F108", id="psc-code"),
    pytest.param("R499", id="other-psc-code"),
    pytest.param("FY2025", id="fiscal-year-code"),
    pytest.param("Q3", id="quarter-code"),
    pytest.param("5th", id="ordinal"),
    pytest.param("22nd", id="two-digit-ordinal"),
    pytest.param("top-5", id="hyphenated-top-n"),
    pytest.param("DBD6KDKXWN65", id="uei-1"),
    pytest.param("VHPJR3MJWV49", id="uei-2"),
    pytest.param("MHMPN3F73NX3", id="uei-3"),
    pytest.param("Z4XGALW7L9K6", id="uei-4"),
    pytest.param("DK4TBEJRAR56", id="uei-5"),
]


@pytest.mark.parametrize("token", NOT_FIGURES)
def test_numbers_inside_words_and_codes_are_not_figures(token: str) -> None:
    assert find_figures(f"See {token} for the detail.") == ([], [])


def test_figures_next_to_codes_are_still_found() -> None:
    figures = read("In FY2025 the PSC F108 of DBD6KDKXWN65 had 6,016 awards.")
    assert [figure.text for figure in figures] == ["6,016"]


def test_iso_dates_are_returned_apart_from_the_figures() -> None:
    figures, dates = find_figures("Between 2024-10-01 and 2025-09-30 there were 6,016 awards.")
    assert dates == ["2024-10-01", "2025-09-30"]
    assert [figure.text for figure in figures] == ["6,016"]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("2024-10-01", id="bare"),
        pytest.param("(2024-10-01)", id="in-brackets"),
        pytest.param("since 2024-10-01, EPA", id="before-a-comma"),
        pytest.param("until 2024-10-01.", id="at-the-end-of-a-sentence"),
    ],
)
def test_an_iso_date_is_found_beside_punctuation_and_is_not_a_number(text: str) -> None:
    assert find_figures(text) == ([], ["2024-10-01"])


def test_figures_are_returned_in_order_of_appearance() -> None:
    figures = read("DBD6KDKXWN65 got $141.7M, VHPJR3MJWV49 got 136.9M, over 6,016 awards.")
    assert [figure.text for figure in figures] == ["$141.7M", "136.9M", "6,016"]


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="a decimal glued to letters is split: '2.5x' yields the figure '2'",
)
@pytest.mark.parametrize(
    ("text", "whole_part"),
    [
        pytest.param("2.5x", 2, id="multiplier"),
        pytest.param("1.7BN", 1, id="upper-case-bn"),
        pytest.param("3.2mm", 3, id="mm-for-million"),
    ],
)
def test_a_decimal_glued_to_letters_is_not_read_as_its_whole_part(
    text: str, whole_part: int
) -> None:
    # Skipped, like '5th', or read in full as '2.5': either is fine. Read as '2', it is not.
    assert [figure for figure in read(text) if figure.value == whole_part] == []


# --- measured values: the metric cell of a row, at the precision written ----------------------


def test_an_exact_amount_is_grounded() -> None:
    assert missing("EPA obligated $1,697,318,332.65.", TOTAL) == []
    assert missing("EPA obligated 1,697,318,332.65 dollars.", TOTAL) == []


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("$1,697,318,333", id="whole-dollars"),
        pytest.param("$1,697.32 million", id="million-two-places"),
        pytest.param("$1,697 million", id="million-whole"),
        pytest.param("$1.70 billion", id="billion-two-places"),
        pytest.param("$1.7 billion", id="billion-one-place"),
        pytest.param("$2 billion", id="billion-whole"),
        pytest.param("1.7bn", id="bn-suffix"),
        pytest.param("$1.7B", id="b-suffix"),
    ],
)
def test_a_rounded_amount_is_grounded_at_the_precision_it_is_written(text: str) -> None:
    assert missing(f"EPA obligated {text}.", TOTAL) == []


@pytest.mark.parametrize("text", ["$1,697,318,332.6", "$1,697,318,332.7"])
def test_a_value_halfway_between_two_roundings_is_accepted_either_way(text: str) -> None:
    # 1,697,318,332.65 sits exactly between .6 and .7, and either may be the rounded form
    assert missing(f"EPA obligated {text}.", TOTAL) == []


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("$1.80 billion", id="wrong-billion"),
        pytest.param("$1.69 billion", id="truncated-not-rounded"),
        pytest.param("$1 billion", id="whole-billion-too-low"),
        pytest.param("$1,697,318,334", id="one-dollar-off"),
        pytest.param("$1,697,318,332.66", id="one-cent-off"),
        pytest.param("$16.97 billion", id="decimal-point-moved"),
        pytest.param("$1.70 million", id="wrong-scale-word"),
    ],
)
def test_a_wrong_rounding_is_ungrounded(text: str) -> None:
    assert missing(f"EPA obligated {text}.", TOTAL) == [text]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("44.9%", id="percent-one-place"),
        pytest.param("44.9 percent", id="percent-word"),
        pytest.param("44.90%", id="percent-two-places"),
        pytest.param("45%", id="percent-whole"),
        pytest.param("0.449", id="fraction-three-places"),
        pytest.param("0.4490", id="fraction-four-places"),
        pytest.param("0.45", id="fraction-two-places"),
    ],
)
def test_a_share_is_grounded_as_a_percent_and_as_a_fraction(text: str) -> None:
    assert missing(f"The top ten hold {text} of it.", SHARE) == []


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("44.8%", id="percent-one-place-off"),
        pytest.param("44.91%", id="percent-two-places-off"),
        pytest.param("44%", id="percent-whole-off"),
        pytest.param("4.49%", id="decimal-point-moved"),
        pytest.param("0.44", id="fraction-off"),
        pytest.param("45", id="number-without-a-percent-sign"),
    ],
)
def test_a_share_written_at_the_wrong_value_is_ungrounded(text: str) -> None:
    assert missing(f"The top ten hold {text} of it.", SHARE) == [text]


@pytest.mark.parametrize(
    ("run", "text"),
    [
        pytest.param(COUNT, "6,016", id="count-with-separator"),
        pytest.param(COUNT, "6016", id="count-without-separator"),
        pytest.param(COUNT, "6 thousand", id="count-in-thousands"),
        pytest.param(AVERAGE, "$282,134.03", id="average-to-the-cent"),
        pytest.param(AVERAGE, "$282,134", id="average-in-dollars"),
        pytest.param(AVERAGE, "282k", id="average-in-thousands"),
        pytest.param(RATE, "93%", id="rate-whole-percent"),
        pytest.param(RATE, "92.99%", id="rate-two-places"),
        pytest.param(RATE, "0.93", id="rate-as-a-fraction"),
    ],
)
def test_an_int_or_float_cell_is_grounded_like_a_decimal_one(run: QueryRun, text: str) -> None:
    assert missing(f"It was {text} overall.", run) == []


@pytest.mark.parametrize(
    ("run", "text"),
    [
        pytest.param(COUNT, "6,017", id="count-one-off"),
        pytest.param(COUNT, "6,000", id="count-rounded-without-a-scale-word"),
        pytest.param(AVERAGE, "$282,135", id="average-one-dollar-off"),
        pytest.param(AVERAGE, "$282,134.04", id="average-one-cent-off"),
        pytest.param(RATE, "92%", id="rate-one-point-off"),
        pytest.param(RATE, "0.92", id="rate-fraction-off"),
    ],
)
def test_an_int_or_float_cell_does_not_ground_a_wrong_value(run: QueryRun, text: str) -> None:
    assert missing(f"It was {text} overall.", run) == [text]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("-$47,387.44", id="hyphen-then-dollar"),
        pytest.param("$-47,387.44", id="dollar-then-hyphen"),
        pytest.param(f"{MINUS}$47,387.44", id="unicode-minus-then-dollar"),
        pytest.param("-$47k", id="rounded-in-thousands"),
    ],
)
def test_a_negative_row_is_grounded_when_the_minus_sign_is_kept(text: str) -> None:
    assert missing(f"Transactions with no code netted {text}.", BY_COMPETED) == []


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("$47,387.44", id="exact"),
        pytest.param("$47k", id="rounded-in-thousands"),
    ],
)
def test_a_negative_row_is_not_grounded_by_its_figure_without_the_minus_sign(text: str) -> None:
    assert missing(f"Transactions with no code netted {text}.", BY_COMPETED) == [text]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("-$1.70 billion", id="hyphen"),
        pytest.param(f"{MINUS}$1,697,318,332.65", id="unicode-minus"),
    ],
)
def test_a_positive_row_is_not_grounded_by_its_figure_with_a_minus_sign(text: str) -> None:
    assert missing(f"EPA obligated {text}.", TOTAL) == [text]


FIRST, SECOND = RECIPIENTS[0][1], RECIPIENTS[1][1]
MONTH_4, MONTH_5 = Decimal("311975420.85"), Decimal("151198107.99")
AVERAGE_OF_FIVE = sum((cell for _, cell in RECIPIENTS), Decimal(0)) / len(RECIPIENTS)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(f"${FIRST + SECOND:,.2f}", id="sum-of-two-rows"),
        pytest.param(f"${FIRST - SECOND:,.2f}", id="difference-of-two-rows"),
        pytest.param(f"${AVERAGE_OF_FIVE:,.2f}", id="average-of-the-rows"),
        pytest.param(f"{(MONTH_5 - MONTH_4) / MONTH_4:.1%}", id="growth-rate-between-two-rows"),
    ],
)
def test_a_figure_the_model_computed_from_the_rows_is_ungrounded(text: str) -> None:
    evidence = build_evidence([TOP5, MONTHS])
    assert ungrounded(f"It came to {text}.", evidence) == [text]


# --- frame numbers: plain whole numbers that frame the queries --------------------------------


def test_the_year_and_month_cells_of_a_grain_ground_those_numbers() -> None:
    evidence = build_evidence([MONTHS])
    assert ungrounded("In fiscal year 2025 month 4 led, then month 5.", evidence) == []
    assert ungrounded("In fiscal year 2024 month 6 led.", evidence) == ["2024", "6"]


def test_a_code_written_in_digits_grounds_that_number() -> None:
    evidence = build_evidence([NAICS])
    assert ungrounded("NAICS code 562910 led, ahead of NAICS code 541620.", evidence) == []
    assert ungrounded("NAICS code 541611 came third.", evidence) == ["541611"]


def test_digits_inside_a_code_that_is_not_all_digits_ground_nothing() -> None:
    evidence = build_evidence([PSC, TOP3])
    assert ungrounded("PSC F108 led, then R499.", evidence) == []
    assert ungrounded("PSC 108 and PSC 499 led.", evidence) == ["108", "499"]
    assert ungrounded("DBD6KDKXWN65 led, with 65 awards.", evidence) == ["65"]


@pytest.mark.parametrize(
    ("item", "total"),
    [
        pytest.param(
            {"field": "naics", "op": "eq", "value": "541611"}, "103690261.13", id="one-code"
        ),
        # 541620 and 541611 added up
        pytest.param(
            {"field": "naics", "op": "in", "value": ["541620", "541611"]},
            "453474645.06",
            id="list-of-codes",
        ),
    ],
)
def test_a_numeric_filter_value_grounds_that_number(item: dict[str, Any], total: str) -> None:
    filtered = make_run([(Decimal(total),)], filters=[item])
    assert missing("Looking at NAICS code 541611 only.", filtered) == []
    assert missing("Looking at NAICS code 541512 only.", filtered) == ["541512"]


@pytest.mark.parametrize(
    ("item", "total", "text", "expected"),
    [
        pytest.param(
            {"field": "competed", "op": "eq", "value": False},
            "119071157.35",
            "It was 0.",
            ["0"],
            id="false-flag",
        ),
        # F108 and R499 added up
        pytest.param(
            {"field": "psc", "op": "in", "value": ["F108", "R499"]},
            "942724821.45",
            "PSC 108 and PSC 499 only.",
            ["108", "499"],
            id="codes-with-letters",
        ),
    ],
)
def test_a_filter_value_that_is_not_a_number_grounds_no_number(
    item: dict[str, Any], total: str, text: str, expected: list[str]
) -> None:
    filtered = make_run([(Decimal(total),)], filters=[item])
    assert missing(text, filtered) == expected


def test_the_limit_of_a_query_grounds_that_number() -> None:
    # three rows under a limit of ten: the filters left fewer than ten recipients
    limited = make_run(RECIPIENTS[:3], RECIPIENT_COLUMNS, dimensions=["recipient"], limit=10)
    assert missing("These are the top 10 recipients.", limited) == []
    assert missing("These are the top 10 recipients.", TOP3) == ["10"]
    assert missing("These are the top 11 recipients.", limited) == ["11"]


def test_the_n_of_a_top_n_metric_grounds_that_number() -> None:
    text = "The top 10 recipients hold 44.9%."
    assert missing(text, SHARE, top_n=[10]) == []
    assert missing(text, SHARE) == ["10"]


@pytest.mark.parametrize("rank", ["1", "2", "3"])
def test_a_rank_from_one_to_the_number_of_rows_is_grounded(rank: str) -> None:
    assert missing(f"Rank {rank} went to a large recipient.", TOP3) == []


def test_a_rank_beyond_the_number_of_rows_is_ungrounded() -> None:
    assert missing("Rank 4 went to a large recipient.", TOP3) == ["4"]


@pytest.mark.parametrize("part", ["2025", "3", "31"])
def test_year_month_and_day_of_the_time_range_are_grounded(part: str) -> None:
    assert missing(f"The number {part} appears.", QUARTER) == []


@pytest.mark.parametrize("part", ["2024", "9", "30"])
def test_the_parts_of_the_coverage_are_not_grounded_by_another_time_range(part: str) -> None:
    assert missing(f"The number {part} appears.", QUARTER) == [part]


@pytest.mark.parametrize("part", ["2024", "10", "1", "2025", "9", "30"])
def test_year_month_and_day_of_the_coverage_are_grounded(part: str) -> None:
    assert missing(f"The number {part} appears.", coverage=COVERAGE) == []


@pytest.mark.parametrize("part", ["2023", "2", "11", "12", "31"])
def test_a_number_that_is_not_a_part_of_the_coverage_is_ungrounded(part: str) -> None:
    assert missing(f"The number {part} appears.", coverage=COVERAGE) == [part]


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("4.0", id="with-a-decimal"),
        pytest.param("4.00", id="with-two-decimals"),
        pytest.param("4%", id="percent"),
        pytest.param("$4", id="dollars"),
        pytest.param("4 million", id="scale-word"),
        pytest.param("4k", id="scale-letter"),
        pytest.param("-4", id="negative"),
    ],
)
def test_a_frame_number_must_be_written_as_a_plain_whole_number(text: str) -> None:
    evidence = build_evidence([MONTHS])
    assert ungrounded("month 4", evidence) == []
    assert ungrounded(f"month {text}", evidence) == [text]


def test_a_boolean_cell_is_not_a_frame_number() -> None:
    # False would read as 0 if a boolean counted as a whole number
    assert missing("It was 0.", BY_COMPETED) == ["0"]


# --- dates ------------------------------------------------------------------------------------


def test_an_iso_date_of_the_time_range_is_grounded() -> None:
    assert missing("From 2024-10-01 to 2025-09-30, EPA obligated $1.70 billion.", TOTAL_FY) == []


def test_an_iso_date_of_the_coverage_is_grounded() -> None:
    assert missing("The data runs from 2024-10-01 to 2025-09-30.", coverage=COVERAGE) == []


@pytest.mark.parametrize(
    "date",
    [
        pytest.param("2025-10-01", id="day-after-the-end"),
        pytest.param("2024-10-02", id="day-after-the-start"),
        pytest.param("2025-03-31", id="inside-the-range-but-not-an-end"),
    ],
)
def test_another_iso_date_is_ungrounded_even_if_its_parts_are_frame_numbers(date: str) -> None:
    assert missing(f"Obligations ran until {date}.", TOTAL_FY, coverage=COVERAGE) == [date]


def test_an_iso_date_the_question_wrote_is_grounded() -> None:
    question = "What was obligated on 2025-03-31?"
    assert missing("Nothing is reported for 2025-03-31 alone.", TOTAL, question=question) == []
    assert missing("Nothing is reported for 2025-03-30.", TOTAL, question=question) == [
        "2025-03-30"
    ]


# --- figures of the question ------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("2 billion", id="as-written"),
        pytest.param("2.0 billion", id="with-a-decimal"),
        pytest.param("$2,000,000,000", id="in-dollars"),
        pytest.param("$2000 million", id="in-millions"),
    ],
)
def test_a_figure_the_question_wrote_is_grounded_in_any_notation_of_its_value(text: str) -> None:
    question = "Did EPA obligate over 2 billion dollars?"
    sentence = f"Nothing is known about {text}."
    assert missing(sentence, question=question) == []
    assert missing(sentence) == [text]


def test_a_figure_the_question_did_not_write_is_ungrounded() -> None:
    question = "Did EPA obligate over 2 billion dollars?"
    assert missing("Nothing is known about 3 billion.", question=question) == ["3 billion"]


def test_a_year_the_question_wrote_is_grounded_even_when_no_query_holds_it() -> None:
    question = "How much did EPA spend in fiscal year 2023?"
    assert missing("There is no data for fiscal year 2023.", question=question) == []
    assert missing("There is no data for fiscal year 2022.", question=question) == ["2022"]


# --- cells that are not numbers ---------------------------------------------------------------


@pytest.mark.parametrize(
    "cell",
    [
        pytest.param(None, id="null"),
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="infinity"),
        pytest.param(float("-inf"), id="negative-infinity"),
        pytest.param(True, id="boolean"),
        pytest.param("12", id="text"),
    ],
)
def test_a_metric_cell_that_is_not_a_number_is_not_a_measured_value(cell: object) -> None:
    evidence = build_evidence([make_run([(cell,)])])
    assert evidence.measured == ()
    assert ungrounded("It was $12 or 12.", evidence) == ["$12", "12"]


@pytest.mark.xfail(
    strict=True,
    raises=InvalidOperation,
    reason="a Decimal NaN cell is kept as a measured value and makes ungrounded() raise",
)
def test_a_decimal_nan_cell_does_not_stop_the_check() -> None:
    # Not reachable from a DuckDB DECIMAL column; a float NaN is dropped, and so should this be.
    assert missing("It was $12.", make_run([(Decimal("NaN"),)])) == ["$12"]


def test_a_float_cell_is_kept_as_its_shortest_decimal() -> None:
    evidence = build_evidence([SHARE, AVERAGE])
    assert evidence.measured == (Decimal("0.4490202395623079"), Decimal("282134.03135804523"))


# --- the evidence -----------------------------------------------------------------------------


def test_evidence_without_runs_holds_only_the_coverage_dates() -> None:
    evidence = build_evidence([], coverage=COVERAGE)
    assert evidence.measured == ()
    assert evidence.stated == frozenset()
    assert evidence.dates == frozenset({"2024-10-01", "2025-09-30"})
    assert evidence.frame == frozenset(Decimal(n) for n in (2024, 10, 1, 2025, 9, 30))


def test_evidence_of_nothing_is_empty() -> None:
    assert build_evidence([]) == Evidence((), frozenset(), frozenset(), frozenset())


def test_evidence_holds_the_values_the_frame_the_stated_figures_and_the_dates() -> None:
    evidence = build_evidence(
        [MONTHS, TOP5],
        coverage=COVERAGE,
        top_n=[10],
        question="Over 2 billion in fiscal year 2023, as of 2025-03-31?",
    )
    assert evidence.measured == (
        Decimal("311975420.85"),
        Decimal("151198107.99"),
        *(cell for _, cell in RECIPIENTS),
    )
    # Year 2025 and months 4 and 5 of the grain cells, ranks 1 to 5 (and the limit 5), the top-n
    # 10, and the year, month and day of the time range and of the coverage.
    assert evidence.frame == frozenset(Decimal(n) for n in (1, 2, 3, 4, 5, 9, 10, 30, 2024, 2025))
    assert evidence.stated == frozenset({Decimal(2_000_000_000), Decimal(2023)})
    assert evidence.dates == frozenset({"2024-10-01", "2025-09-30", "2025-03-31"})


def test_every_ungrounded_figure_is_named_as_written_in_order() -> None:
    evidence = build_evidence([TOTAL])
    text = "EPA obligated $1.80 billion over 6,016 awards, which is 44.9%."
    assert ungrounded(text, evidence) == ["$1.80 billion", "6,016", "44.9%"]


def test_a_text_without_figures_is_grounded() -> None:
    assert missing("EPA obligated a large sum.") == []
    assert missing("") == []


def test_a_full_answer_is_grounded_by_the_rows_of_its_query() -> None:
    text = (
        "In fiscal year 2025 the largest of the top 5 recipients, at the parent level, was "
        "DBD6KDKXWN65, with $141.7 million, ahead of VHPJR3MJWV49 with $136.9 million. The "
        "fifth, DK4TBEJRAR56, received $66,893,110.69."
    )
    assert missing(text, TOP5) == []
