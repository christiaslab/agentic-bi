"""The final reply: how it is parsed, and the rules it must keep before it is accepted.

``parse_final_reply`` reads one JSON object, an answer or a refusal, and tolerates a code fence or
text around it. ``check_final_reply`` then holds the reply to the rows of the queries that ran.
The queries are built by hand with golden values of docs/golden/golden.json: no database, no
model, no network.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

import pytest

from agentic_bi.agent.answer import (
    ANSWERED,
    COMPILER_REFUSAL_CODES,
    REFUSAL_CODES,
    REFUSED,
    QueryRun,
)
from agentic_bi.agent.final import (
    MAX_ANSWER_CHARS,
    MAX_REASON_CHARS,
    FinalReply,
    FinalReplyRejected,
    check_final_reply,
    feedback,
    parse_final_reply,
)

FY = {"start": "2024-10-01", "end": "2025-09-30"}
COVERAGE = (dt.date(2024, 10, 1), dt.date(2025, 9, 30))
ANSWER = "EPA obligated $1,697,318,332.65 in fiscal year 2025."
REASON = "The warehouse holds one fiscal year only."


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


# The first run has a time range, so the year of the fiscal year is a frame number; the others
# have none, so a year or a count can reach the evidence only through the settings of a check.
TOTAL_FY = make_run([(Decimal("1697318332.65"),)], time_range=FY)
TOTAL = make_run([(Decimal("1697318332.65"),)])
COUNT = make_run([(6016,)], ("award_count",))
SHARE = make_run([(0.4490202395623079,)], ("supplier_concentration_top10",))
TOP2 = make_run(
    [("DBD6KDKXWN65", Decimal("141735682.10")), ("VHPJR3MJWV49", Decimal("136907984.75"))],
    ("recipient", "obligations"),
    dimensions=["recipient"],
)


def json_answer(text: object = ANSWER) -> str:
    return json.dumps({"status": "answered", "answer": text})


def json_refusal(code: object = "outside_coverage", reason: object = REASON) -> str:
    return json.dumps({"status": "refused", "code": code, "reason": reason})


def json_with_text(key: str, value: object) -> str:
    """A reply of the right shape for ``key`` ("answer" or "reason"), with that field replaced."""
    return json_answer(value) if key == "answer" else json_refusal(reason=value)


def parse_problem(text: str) -> FinalReplyRejected:
    with pytest.raises(FinalReplyRejected) as caught:
        parse_final_reply(text)
    return caught.value


def answered(text: str) -> FinalReply:
    return FinalReply(ANSWERED, answer=text)


def refused(reason: str, code: str = "outside_coverage") -> FinalReply:
    return FinalReply(REFUSED, code=code, reason=reason)


def check(
    reply: FinalReply,
    *runs: QueryRun,
    question: str = "How much did EPA obligate?",
    **settings: Any,
) -> None:
    check_final_reply(reply, runs=runs, question=question, **settings)


def check_problem(
    reply: FinalReply,
    *runs: QueryRun,
    question: str = "How much did EPA obligate?",
    **settings: Any,
) -> FinalReplyRejected:
    with pytest.raises(FinalReplyRejected) as caught:
        check_final_reply(reply, runs=runs, question=question, **settings)
    return caught.value


# --- parsing: what is read ---------------------------------------------------------------------


def test_a_plain_json_answer_is_read() -> None:
    assert parse_final_reply(json_answer()) == FinalReply(ANSWERED, answer=ANSWER)


def test_a_plain_json_refusal_is_read() -> None:
    reply = parse_final_reply(json_refusal())
    assert reply == FinalReply(REFUSED, code="outside_coverage", reason=REASON)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(f"```json\n{json_answer()}\n```", id="json-fence"),
        pytest.param(f"```\n{json_answer()}\n```", id="bare-fence"),
        pytest.param(f"\n  ```json\n{json_answer()}\n```  \n", id="blank-edges"),
        pytest.param(f"```json\r\n{json_answer()}\r\n```", id="windows-line-ends"),
        pytest.param(f"```json\n{json_answer()}\n```\nHope that helps.", id="prose-after"),
        pytest.param(f"Here is my answer:\n```json\n{json_answer()}\n```", id="prose-before"),
    ],
)
def test_a_json_reply_in_a_code_fence_is_read_and_the_fence_is_not_kept(text: str) -> None:
    assert parse_final_reply(text) == FinalReply(ANSWERED, answer=ANSWER)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(f"Here is my answer:\n{json_answer()}", id="prose-before"),
        pytest.param(f"{json_answer()}\nHope that helps.", id="prose-after"),
        pytest.param(f"Here you go: {json_answer()} Done.", id="prose-on-both-sides"),
    ],
)
def test_a_json_reply_with_prose_around_it_is_read_and_the_prose_is_not_kept(text: str) -> None:
    assert parse_final_reply(text) == FinalReply(ANSWERED, answer=ANSWER)


def test_a_fenced_refusal_is_read() -> None:
    assert parse_final_reply(f"```json\n{json_refusal()}\n```").code == "outside_coverage"


def test_pretty_printed_json_is_read() -> None:
    text = json.dumps({"status": "answered", "answer": ANSWER}, indent=2)
    assert parse_final_reply(text) == FinalReply(ANSWERED, answer=ANSWER)


def test_the_answer_and_the_reason_are_returned_stripped() -> None:
    assert parse_final_reply(json_answer("  \n" + ANSWER + "\n ")).answer == ANSWER
    assert parse_final_reply(json_refusal(reason=f" {REASON}\n")).reason == REASON


def test_the_refusal_codes_are_the_compiler_codes_and_outside_coverage() -> None:
    # parametrizing over an empty tuple would skip silently, so the tuple is checked here
    assert COMPILER_REFUSAL_CODES
    assert REFUSAL_CODES == (*COMPILER_REFUSAL_CODES, "outside_coverage")
    assert len(set(REFUSAL_CODES)) == len(REFUSAL_CODES)


@pytest.mark.parametrize("code", REFUSAL_CODES)
def test_a_refusal_is_read_for_each_refusal_code(code: str) -> None:
    reply = parse_final_reply(json_refusal(code=code))
    assert reply == FinalReply(REFUSED, code=code, reason=REASON)


# --- parsing: what is rejected -----------------------------------------------------------------

NOT_ONE_OBJECT = [
    pytest.param("EPA obligated $1.70 billion in fiscal year 2025.", id="plain-text"),
    pytest.param("", id="empty"),
    pytest.param("   \n", id="blank"),
    pytest.param('["answered", "EPA obligated $1.70 billion."]', id="list-of-strings"),
    pytest.param("[1, 2, 3]", id="list-of-numbers"),
    pytest.param("42", id="number"),
    pytest.param('"answered"', id="string"),
    pytest.param("null", id="null"),
    pytest.param(f"{json_answer()}\n{json_answer()}", id="two-objects"),
    pytest.param(json_answer()[:-1], id="truncated-object"),
    pytest.param("{'status': 'answered', 'answer': 'x'}", id="single-quotes"),
]


@pytest.mark.parametrize("text", NOT_ONE_OBJECT)
def test_a_reply_that_is_not_one_json_object_is_rejected(text: str) -> None:
    assert str(parse_problem(text)) == "the final reply must be exactly one JSON object"


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({"status": "maybe", "answer": "x"}, id="unknown-status"),
        pytest.param({"status": "ANSWERED", "answer": "x"}, id="wrong-case"),
        pytest.param({"status": "failed", "answer": "x"}, id="status-the-harness-owns"),
        pytest.param({"answer": "x"}, id="missing-status"),
        pytest.param({"status": None, "answer": "x"}, id="null-status"),
        pytest.param({"status": 7, "answer": "x"}, id="number-status"),
        pytest.param({"status": ["answered"], "answer": "x"}, id="list-status"),
        pytest.param({}, id="empty-object"),
    ],
)
def test_an_unknown_or_missing_status_is_rejected(data: dict[str, Any]) -> None:
    assert str(parse_problem(json.dumps(data))) == '"status" must be "answered" or "refused"'


@pytest.mark.parametrize(
    ("data", "keys"),
    [
        pytest.param(
            {"status": "answered", "answer": "x", "sql": "SELECT 1"}, "answer, status", id="sql"
        ),
        pytest.param(
            {"status": "answered", "answer": "x", "code": "outside_coverage"},
            "answer, status",
            id="code-on-an-answer",
        ),
        pytest.param(
            {"status": "refused", "code": "outside_coverage", "reason": "x", "answer": "x"},
            "code, reason, status",
            id="answer-on-a-refusal",
        ),
        pytest.param(
            {"status": "refused", "code": "outside_coverage", "reason": "x", "note": "x"},
            "code, reason, status",
            id="note-on-a-refusal",
        ),
    ],
)
def test_a_reply_with_an_extra_key_is_rejected(data: dict[str, Any], keys: str) -> None:
    message = str(parse_problem(json.dumps(data)))
    assert "has exactly the keys" in message
    assert message.endswith(keys)


@pytest.mark.parametrize(
    ("data", "keys"),
    [
        pytest.param({"status": "answered"}, "answer, status", id="answer-without-answer"),
        pytest.param(
            {"status": "refused", "code": "outside_coverage"},
            "code, reason, status",
            id="refusal-without-reason",
        ),
        pytest.param(
            {"status": "refused", "reason": "x"}, "code, reason, status", id="refusal-without-code"
        ),
        pytest.param({"status": "refused"}, "code, reason, status", id="refusal-with-only-status"),
    ],
)
def test_a_reply_with_a_missing_key_is_rejected(data: dict[str, Any], keys: str) -> None:
    message = str(parse_problem(json.dumps(data)))
    assert "has exactly the keys" in message
    assert message.endswith(keys)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("", id="empty"),
        pytest.param("  \n ", id="blank"),
        pytest.param(None, id="null"),
        pytest.param(42, id="number"),
        pytest.param(["EPA obligated $1.70 billion."], id="list"),
        pytest.param({"text": "x"}, id="object"),
    ],
)
@pytest.mark.parametrize("key", ["answer", "reason"])
def test_an_answer_or_a_reason_that_is_not_non_empty_text_is_rejected(
    key: str, value: object
) -> None:
    assert str(parse_problem(json_with_text(key, value))) == f'"{key}" must be non-empty text'


@pytest.mark.parametrize(
    ("key", "limit"), [("answer", MAX_ANSWER_CHARS), ("reason", MAX_REASON_CHARS)]
)
def test_a_text_of_exactly_the_length_limit_is_accepted(key: str, limit: int) -> None:
    reply = parse_final_reply(json_with_text(key, "a" * limit))
    assert getattr(reply, key) == "a" * limit


@pytest.mark.parametrize(
    ("key", "limit"), [("answer", MAX_ANSWER_CHARS), ("reason", MAX_REASON_CHARS)]
)
def test_a_text_over_the_length_limit_is_rejected(key: str, limit: int) -> None:
    problem = parse_problem(json_with_text(key, "a" * (limit + 1)))
    assert str(problem) == f'"{key}" must be at most {limit} characters'


@pytest.mark.parametrize(
    "code",
    [
        pytest.param("out_of_scope", id="invented-code"),
        pytest.param("invalid_tool_input", id="code-of-a-tool-error"),
        pytest.param("refused", id="code-of-the-base-class"),
        pytest.param("OUTSIDE_COVERAGE", id="wrong-case"),
        pytest.param("", id="empty"),
        pytest.param(None, id="null"),
        pytest.param(7, id="number"),
        pytest.param(["outside_coverage"], id="list"),
    ],
)
def test_a_code_that_is_not_a_refusal_code_is_rejected_and_the_codes_are_listed(
    code: object,
) -> None:
    message = str(parse_problem(json_refusal(code=code)))
    assert message.startswith(f"{code!r} is not a refusal code")
    for allowed in REFUSAL_CODES:
        assert allowed in message


# --- checking: an answer needs a query, and every figure in the rows --------------------------


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("EPA obligated $1.70 billion.", id="with-a-figure"),
        pytest.param("EPA obligated a large sum.", id="without-a-figure"),
    ],
)
def test_an_answer_with_no_query_run_is_rejected(text: str) -> None:
    problem = check_problem(answered(text))
    assert str(problem) == "an answer needs at least one run_query result; run the query, or refuse"


def test_an_answer_whose_figures_are_in_the_rows_passes() -> None:
    reply = answered("EPA obligated $1,697,318,332.65, about $1.70 billion, in fiscal year 2025.")
    assert check(reply, TOTAL_FY) is None


def test_an_answer_may_use_the_figures_of_any_query_that_ran() -> None:
    reply = answered("EPA obligated $1.70 billion over 6,016 active awards.")
    assert check(reply, TOTAL_FY, COUNT) is None
    assert "6,016" in str(check_problem(reply, TOTAL_FY))


def test_an_answer_without_figures_passes_when_a_query_ran() -> None:
    assert check(answered("EPA obligated a large sum."), TOTAL_FY) is None


def test_an_answer_with_an_ungrounded_figure_is_rejected_and_the_message_names_it() -> None:
    reply = answered("EPA obligated $1.80 billion in fiscal year 2025.")
    message = str(check_problem(reply, TOTAL_FY))
    assert message.startswith("these figures do not appear in the rows of any query you ran: ")
    assert "$1.80 billion" in message
    # a grounded figure of the same sentence is not blamed
    assert "2025" not in message


def test_every_ungrounded_figure_is_named_as_written() -> None:
    reply = answered("EPA obligated $1.80 billion over 99 awards, or 44.9% of its budget.")
    message = str(check_problem(reply, TOTAL_FY))
    assert "$1.80 billion; 99; 44.9%" in message


def test_a_rounded_figure_is_accepted_and_a_wrongly_rounded_one_is_not() -> None:
    assert check(answered("EPA obligated $1.7 billion."), TOTAL_FY) is None
    assert "$1.6 billion" in str(check_problem(answered("EPA obligated $1.6 billion."), TOTAL_FY))


def test_a_sum_the_model_computed_is_rejected() -> None:
    reply = answered("The top two recipients together hold $278,643,666.85.")
    assert "$278,643,666.85" in str(check_problem(reply, TOP2))


# --- checking: a refusal reason is held to the same figures -----------------------------------


def test_a_refusal_needs_no_query() -> None:
    assert check(refused("The catalog has no metric for new awards.")) is None


def test_a_refusal_reason_with_an_ungrounded_figure_is_rejected() -> None:
    reply = refused("The catalog counts awards active in the period (6,016), not new awards.")
    assert "6,016" in str(check_problem(reply))


def test_a_refusal_reason_may_use_a_figure_a_query_returned() -> None:
    reply = refused("The catalog counts awards active in the period (6,016), not new awards.")
    assert check(reply, COUNT) is None


def test_a_refusal_reason_repeating_a_year_from_the_question_passes() -> None:
    reply = refused("The warehouse holds no data for fiscal year 2023.")
    assert check(reply, question="How much did EPA spend in fiscal year 2023?") is None
    # the question is what grounds the year: the same reason after another question is rejected
    assert "2023" in str(check_problem(reply, question="How much did EPA spend last year?"))


def test_a_refusal_reason_with_a_year_the_question_did_not_write_is_rejected() -> None:
    reply = refused("The warehouse holds no data for fiscal year 2022.")
    question = "How much did EPA spend in fiscal year 2023?"
    assert "2022" in str(check_problem(reply, question=question))


def test_a_refusal_reason_may_name_the_covered_year_when_the_coverage_is_passed() -> None:
    reply = refused("The warehouse holds fiscal year 2025 only, not fiscal year 2023.")
    question = "How much did EPA spend in fiscal year 2023?"
    assert check(reply, question=question, coverage=COVERAGE) is None
    assert "2025" in str(check_problem(reply, question=question))


def test_a_refusal_reason_naming_the_coverage_dates_passes() -> None:
    reply = refused("The warehouse holds action dates from 2024-10-01 to 2025-09-30 only.")
    assert check(reply, coverage=COVERAGE) is None
    assert "2024-10-01; 2025-09-30" in str(check_problem(reply))


def test_the_code_of_a_refusal_does_not_change_what_its_reason_may_say() -> None:
    reply = refused("The catalog counts 6,016 awards.", code="unknown_metric")
    assert "6,016" in str(check_problem(reply))


# --- checking: the settings reach the evidence ------------------------------------------------


def test_the_question_grounds_a_year_of_the_answer_and_no_other_number() -> None:
    reply = answered("In fiscal year 2023 there is nothing; fiscal year 2025 held $1.70 billion.")
    assert check(reply, TOTAL_FY, question="What about fiscal year 2023?") is None
    assert "2023" in str(check_problem(reply, TOTAL_FY, question="What about last year?"))
    count = answered("Yes, the top 3 held $1.70 billion.")
    assert "3" in str(check_problem(count, TOTAL_FY, question="Did the top 3 hold it all?"))


def test_an_amount_of_the_question_is_not_grounded_by_the_question() -> None:
    reply = answered("No: EPA obligated $1.70 billion, below the 3 billion you asked about.")
    problem = check_problem(reply, TOTAL_FY, question="Did EPA obligate over 3 billion?")
    assert "3 billion" in str(problem)


def test_the_coverage_grounds_the_year_of_an_answer() -> None:
    reply = answered("EPA obligated $1.70 billion in fiscal year 2025.")
    assert check(reply, TOTAL, coverage=COVERAGE) is None
    assert "2025" in str(check_problem(reply, TOTAL))


def test_the_top_n_grounds_the_n_of_an_answer() -> None:
    reply = answered("The top 10 recipients hold 44.9% of net obligations.")
    assert check(reply, SHARE, top_n=[10]) is None
    assert "10" in str(check_problem(reply, SHARE))


def test_the_names_the_schema_tool_returned_reach_the_figure_check() -> None:
    name = "CHENEGA SENTINEL 360, LLC"
    reply = answered(f"The largest recipient was {name}, with $141.7 million.")
    assert check(reply, TOP2, names=[name]) is None
    assert "360" in str(check_problem(reply, TOP2))


# --- feedback ---------------------------------------------------------------------------------


def test_feedback_starts_with_the_harness_tag_and_holds_the_problem_of_a_check() -> None:
    problem = check_problem(answered("EPA obligated $1.80 billion."), TOTAL_FY)
    note = feedback(problem)
    assert note.startswith("[harness check]")
    assert str(problem) in note
    assert "$1.80 billion" in note
    assert "exactly one JSON object" in note


def test_feedback_holds_the_problem_of_a_parse() -> None:
    problem = parse_problem("The total is $1.70 billion.")
    note = feedback(problem)
    assert note.startswith("[harness check]")
    assert str(problem) in note


# --- parsing then checking, as the loop does --------------------------------------------------


def test_a_reply_that_parses_is_then_held_to_the_rows() -> None:
    good = parse_final_reply(json_answer("EPA obligated $1.70 billion in fiscal year 2025."))
    assert check(good, TOTAL_FY) is None
    bad = parse_final_reply(json_answer("EPA obligated $1.80 billion in fiscal year 2025."))
    assert "$1.80 billion" in str(check_problem(bad, TOTAL_FY))
