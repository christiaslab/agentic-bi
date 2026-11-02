"""The verdict parser: valid answers pass, every malformed shape raises and is never a pass."""

import json
from typing import Any

import pytest

from agentic_bi.verifier.verdict import (
    DETERMINISTIC_ISSUE_CODES,
    MAX_EXPLANATION_LENGTH,
    MAX_ISSUES,
    MODEL_ISSUE_CODES,
    VERDICT_JSON_SCHEMA,
    VERDICTS,
    MalformedVerdictError,
    Verdict,
    parse_verdict,
    validate,
    verdict_json_schema,
)

ISSUE = {"code": "wrong_grain", "field": "grain", "explanation": "The question asks for months."}


def answer(verdict: str, issues: list[dict[str, Any]], rationale: str = "Because.") -> str:
    return json.dumps({"verdict": verdict, "issues": issues, "rationale": rationale})


CONSISTENT = answer("consistent", [])
INCONSISTENT = answer("inconsistent", [ISSUE])
REFUSE = answer(
    "refuse_expected",
    [{"code": "should_refuse", "field": None, "explanation": "No such measure."}],
)


def test_consistent_verdict_parses() -> None:
    verdict = parse_verdict(CONSISTENT, judge="model-x", prompt_version="v1")
    assert verdict == Verdict(
        verdict="consistent",
        issues=(),
        rationale="Because.",
        stage="model",
        judge="model-x",
        prompt_version="v1",
    )
    assert verdict.codes == ()


def test_inconsistent_verdict_parses_with_its_issue() -> None:
    verdict = parse_verdict(INCONSISTENT)
    assert verdict.verdict == "inconsistent"
    assert verdict.codes == ("wrong_grain",)
    assert verdict.issues[0].field == "grain"
    assert verdict.judge is None
    assert verdict.prompt_version is None


def test_refuse_expected_verdict_parses_with_a_null_field() -> None:
    verdict = parse_verdict(REFUSE)
    assert verdict.verdict == "refuse_expected"
    assert verdict.codes == ("should_refuse",)
    assert verdict.issues[0].field is None


def test_verdict_to_dict_is_json_ready() -> None:
    data = parse_verdict(INCONSISTENT, judge="m", prompt_version="v1").to_dict()
    assert json.loads(json.dumps(data)) == data
    assert data["stage"] == "model"
    assert data["issues"][0] == ISSUE


@pytest.mark.parametrize("opening", ["```json", "```"])
def test_one_fenced_block_is_tolerated(opening: str) -> None:
    text = f"\n{opening}\n{CONSISTENT}\n```\n"
    assert parse_verdict(text).verdict == "consistent"


def test_prose_around_a_fenced_block_is_not_tolerated() -> None:
    with pytest.raises(MalformedVerdictError):
        parse_verdict(f"Here it is:\n```json\n{CONSISTENT}\n```")


def make(**changes: Any) -> str:
    data: dict[str, Any] = {"verdict": "inconsistent", "issues": [dict(ISSUE)], "rationale": "r"}
    data.update(changes)
    return json.dumps(data)


def with_issue(**changes: Any) -> str:
    issue: dict[str, Any] = dict(ISSUE)
    issue.update(changes)
    return make(issues=[issue])


def without(key: str) -> str:
    data = json.loads(INCONSISTENT)
    del data[key]
    return json.dumps(data)


def without_issue_key(key: str) -> str:
    issue = dict(ISSUE)
    del issue[key]
    return make(issues=[issue])


MALFORMED = {
    "not_json": "this is not json",
    "empty_text": "",
    "prose_before": "Verdict: " + CONSISTENT,
    "prose_after": CONSISTENT + "\nHope that helps.",
    "json_array": json.dumps([json.loads(CONSISTENT)]),
    "json_string": json.dumps("consistent"),
    "json_null": "null",
    "missing_verdict": without("verdict"),
    "missing_issues": without("issues"),
    "missing_rationale": without("rationale"),
    "extra_top_level_field": make(confidence=0.9),
    "extra_issue_field": with_issue(severity="high"),
    "missing_issue_code": without_issue_key("code"),
    "missing_issue_field": without_issue_key("field"),
    "missing_issue_explanation": without_issue_key("explanation"),
    "unknown_issue_code": with_issue(code="wrong_vibes"),
    "outside_catalog_by_model": with_issue(code="outside_catalog"),
    "invalid_query_by_model": with_issue(code="invalid_query"),
    "time_range_outside_slice_by_model": with_issue(code="time_range_outside_slice"),
    "unknown_verdict": make(verdict="maybe"),
    "consistent_with_issues": make(verdict="consistent"),
    "inconsistent_without_issue": make(issues=[]),
    "should_refuse_with_inconsistent": make(
        issues=[{"code": "should_refuse", "field": None, "explanation": "No."}]
    ),
    "refuse_expected_without_should_refuse": make(verdict="refuse_expected"),
    "empty_rationale": make(rationale=""),
    "over_long_rationale": make(rationale="x" * 2001),
    "empty_explanation": with_issue(explanation=""),
    "over_long_explanation": with_issue(explanation="x" * (MAX_EXPLANATION_LENGTH + 1)),
    "over_long_field": with_issue(field="x" * 201),
    "too_many_issues": make(issues=[dict(ISSUE) for _ in range(MAX_ISSUES + 1)]),
    "verdict_is_a_number": make(verdict=1),
    "rationale_is_a_number": make(rationale=7),
    "issues_is_an_object": make(issues={}),
    "explanation_is_a_number": with_issue(explanation=3),
    "field_is_a_number": with_issue(field=3),
    "duplicate_top_level_key": (
        '{"verdict": "consistent", "verdict": "consistent", "issues": [], "rationale": "r"}'
    ),
    "duplicate_issue_key": (
        '{"verdict": "inconsistent", "issues": [{"code": "wrong_grain", "code": "wrong_grain", '
        '"field": null, "explanation": "e"}], "rationale": "r"}'
    ),
    "nan_constant": '{"verdict": "consistent", "issues": [], "rationale": "r", "x": NaN}',
    "infinity_constant": '{"verdict": "consistent", "issues": [], "rationale": Infinity}',
}


@pytest.mark.parametrize("text", MALFORMED.values(), ids=MALFORMED.keys())
def test_malformed_answer_raises_and_is_never_a_pass(text: str) -> None:
    with pytest.raises(MalformedVerdictError):
        parse_verdict(text)


@pytest.mark.parametrize("value", [None, 5, 1.5, True, [], {}, b"{}", CONSISTENT.encode()])
def test_non_string_input_raises(value: Any) -> None:
    with pytest.raises(MalformedVerdictError) as info:
        parse_verdict(value)
    assert info.value.where == "response"


def test_error_names_the_path_and_message() -> None:
    with pytest.raises(MalformedVerdictError) as info:
        parse_verdict(with_issue(code="outside_catalog"))
    assert info.value.where == "verdict.issues[0].code"
    assert str(info.value).startswith("verdict.issues[0].code: ")


def test_exactly_max_issues_is_accepted() -> None:
    text = make(issues=[dict(ISSUE) for _ in range(MAX_ISSUES)])
    assert len(parse_verdict(text).issues) == MAX_ISSUES


def test_every_model_code_is_accepted_with_inconsistent() -> None:
    for code in MODEL_ISSUE_CODES:
        if code == "should_refuse":
            continue
        assert parse_verdict(with_issue(code=code)).codes == (code,)


def test_refuse_expected_may_carry_other_issues_beside_should_refuse() -> None:
    issues = [
        {"code": "wrong_metric", "field": "metric", "explanation": "e"},
        {"code": "should_refuse", "field": None, "explanation": "e"},
    ]
    assert parse_verdict(answer("refuse_expected", issues)).codes == (
        "wrong_metric",
        "should_refuse",
    )


# --- The validator and the schema -------------------------------------------------------


def test_validate_raises_value_error_on_an_unsupported_keyword() -> None:
    with pytest.raises(ValueError, match="unsupported schema keywords"):
        validate("x", {"type": "string", "pattern": "^x$"})


def test_validate_raises_value_error_on_an_unsupported_keyword_in_a_subschema() -> None:
    schema = {"type": "object", "properties": {"a": {"type": "string", "format": "date"}}}
    with pytest.raises(ValueError, match="unsupported schema keywords"):
        validate({"a": "x"}, schema)


def test_validate_raises_value_error_on_an_unsupported_type() -> None:
    with pytest.raises(ValueError, match="unsupported schema type"):
        validate(1.5, {"type": "number"})


def test_validate_does_not_treat_a_bool_as_an_integer() -> None:
    validate(3, {"type": "integer"})
    with pytest.raises(MalformedVerdictError):
        validate(True, {"type": "integer"})


def test_schema_issue_code_enum_is_the_model_codes() -> None:
    enum = VERDICT_JSON_SCHEMA["properties"]["issues"]["items"]["properties"]["code"]["enum"]
    assert enum == list(MODEL_ISSUE_CODES)


def test_schema_verdict_enum_is_the_verdicts() -> None:
    assert VERDICT_JSON_SCHEMA["properties"]["verdict"]["enum"] == list(VERDICTS)


def test_schema_closes_both_objects_and_requires_every_field() -> None:
    assert VERDICT_JSON_SCHEMA["additionalProperties"] is False
    assert set(VERDICT_JSON_SCHEMA["required"]) == {"verdict", "issues", "rationale"}
    item = VERDICT_JSON_SCHEMA["properties"]["issues"]["items"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {"code", "field", "explanation"}
    assert VERDICT_JSON_SCHEMA["properties"]["issues"]["maxItems"] == MAX_ISSUES


def test_verdict_json_schema_returns_an_independent_copy() -> None:
    copy = verdict_json_schema()
    assert copy == VERDICT_JSON_SCHEMA
    copy["properties"]["verdict"]["enum"].append("pass_anyway")
    copy["required"].clear()
    assert VERDICT_JSON_SCHEMA["properties"]["verdict"]["enum"] == list(VERDICTS)
    assert VERDICT_JSON_SCHEMA["required"] == ["verdict", "issues", "rationale"]
    assert verdict_json_schema() == VERDICT_JSON_SCHEMA


def test_model_and_deterministic_codes_are_disjoint() -> None:
    assert not set(MODEL_ISSUE_CODES) & set(DETERMINISTIC_ISSUE_CODES)
    assert set(DETERMINISTIC_ISSUE_CODES) == {
        "invalid_query",
        "outside_catalog",
        "time_range_outside_slice",
    }
