"""The replay judge and the hand-written replays: each issue code, a correct query, a refusal."""

import json
from pathlib import Path

import pytest

from agentic_bi.verifier import (
    MODEL_ISSUE_CODES,
    JudgeError,
    MalformedVerdictError,
    ReplayJudge,
    verify,
)
from agentic_bi.verifier.judge import JudgeRequest
from agentic_bi.verifier.prompt import PROMPT_VERSION
from agentic_bi.verifier.questions import load_question_set, normalised_query
from agentic_bi.verifier.replay import (
    ReplayFileError,
    canonical_json,
    load_replays,
    parse_replay,
)

REPLAYS = Path(__file__).resolve().parent / "replays"


@pytest.fixture(scope="module")
def judge() -> ReplayJudge:
    return ReplayJudge.from_directory(REPLAYS)


@pytest.fixture(scope="module")
def replays():
    return {replay.replay_id: replay for replay in load_replays(REPLAYS)}


@pytest.fixture(scope="module")
def questions():
    return load_question_set()


def _run(judge, replays, replay_id):
    replay = replays[replay_id]
    return verify(replay.question, replay.query, judge=judge)


def test_every_replay_is_hand_written_and_says_so(replays) -> None:
    assert replays
    for replay in replays.values():
        assert replay.provenance == "hand_written", replay.replay_id
        assert "not model output" in replay.note, replay.replay_id
        assert replay.prompt_version == PROMPT_VERSION


def test_replay_questions_match_the_question_set(replays, questions) -> None:
    for replay in replays.values():
        assert replay.question_id is not None, replay.replay_id
        assert replay.question == questions.by_id(replay.question_id).question, replay.replay_id


def test_a_correct_query_is_judged_consistent(judge, replays, questions) -> None:
    result = _run(judge, replays, "consistent-total-obligations")
    assert result.checks.passed
    assert result.verdict.verdict == "consistent"
    assert result.verdict.issues == ()
    assert result.verdict.stage == "model"
    assert result.verdict.judge == "replay"
    assert result.verdict.prompt_version == PROMPT_VERSION
    assert replays["consistent-total-obligations"].query == questions.by_id("q01").query


def test_a_null_time_range_equals_the_slice_for_the_judge(judge, replays) -> None:
    assert _run(judge, replays, "consistent-total-obligations-null-range").verdict.verdict == (
        "consistent"
    )


def test_a_top_n_query_passes_the_checks_and_reaches_the_judge(judge, replays) -> None:
    result = _run(judge, replays, "consistent-top5-recipients")
    assert result.checks.passed
    assert result.checks.top_n is not None and result.checks.top_n.limit == 5
    assert result.verdict.verdict == "consistent"


@pytest.mark.parametrize(
    ("replay_id", "code"),
    [
        ("wrong-metric-award-count", "wrong_metric"),
        ("wrong-dimension-award-type", "wrong_dimension"),
        ("missing-filter-california", "missing_filter"),
        ("wrong-filter-psc", "wrong_filter"),
        ("wrong-grain-monthly", "wrong_grain"),
        ("wrong-time-range-march", "wrong_time_range"),
        ("wrong-order-psc-ascending", "wrong_order"),
    ],
)
def test_each_issue_code_on_a_deliberately_wrong_query(
    judge, replays, questions, replay_id, code
) -> None:
    replay = replays[replay_id]
    expected = questions.by_id(replay.question_id).query
    assert normalised_query(replay.query) != normalised_query(expected), "not a wrong query"
    result = _run(judge, replays, replay_id)
    assert result.checks.passed, "a wrong-intent query must pass the deterministic checks"
    assert result.verdict.verdict == "inconsistent"
    assert result.verdict.codes == (code,)


def test_several_issues_in_one_verdict(judge, replays) -> None:
    result = _run(judge, replays, "several-issues-average-quarterly")
    assert result.verdict.verdict == "inconsistent"
    assert set(result.verdict.codes) == {"missing_filter", "wrong_grain"}


@pytest.mark.parametrize(
    "replay_id", ["should-refuse-transactions", "should-refuse-ambiguous-best-month"]
)
def test_a_question_that_should_be_refused(judge, replays, questions, replay_id) -> None:
    replay = replays[replay_id]
    assert questions.by_id(replay.question_id).outcome == "refuse"
    result = _run(judge, replays, replay_id)
    assert result.checks.passed, "the substitute query is valid; only intent can refuse it"
    assert result.verdict.verdict == "refuse_expected"
    assert result.verdict.codes == ("should_refuse",)


def test_every_model_issue_code_is_shown_by_a_replay(judge, replays) -> None:
    seen = set()
    for replay in replays.values():
        if replay.replay_id.startswith("malformed"):
            continue
        seen |= set(verify(replay.question, replay.query, judge=judge).verdict.codes)
    assert seen == set(MODEL_ISSUE_CODES)


@pytest.mark.parametrize("replay_id", ["malformed-prose-answer", "malformed-consistent-with-issue"])
def test_a_malformed_verdict_is_an_error_not_a_pass(judge, replays, replay_id) -> None:
    replay = replays[replay_id]
    with pytest.raises(MalformedVerdictError):
        verify(replay.question, replay.query, judge=judge)


def test_a_request_with_no_replay_is_an_error(judge) -> None:
    with pytest.raises(JudgeError):
        verify("A question no replay answers?", {"metric": "obligations"}, judge=judge)


def test_a_replay_for_another_prompt_version_is_never_served(replays) -> None:
    replay = replays["consistent-total-obligations"]
    request = JudgeRequest("system", "user", replay.question, replay.query, "v0")
    with pytest.raises(JudgeError):
        ReplayJudge([replay]).judge(request)


def test_matching_ignores_key_order_in_the_query(replays) -> None:
    replay = replays["wrong-filter-psc"]
    reordered = json.loads(json.dumps(dict(reversed(list(replay.query.items())))))
    request = JudgeRequest("s", "u", replay.question, reordered, PROMPT_VERSION)
    assert canonical_json(reordered) == canonical_json(replay.query)
    assert ReplayJudge([replay]).judge(request) == replay.response


def test_the_judge_is_never_called_when_a_check_fails() -> None:
    class Exploding:
        name = "exploding"

        def judge(self, request):
            raise AssertionError("the judge must not be called")

    result = verify("How many transactions?", {"metric": "transaction_count"}, judge=Exploding())
    assert result.verdict.stage == "deterministic"
    assert result.verdict.codes == ("outside_catalog",)


def _valid() -> dict:
    return {
        "replay_id": "r1",
        "provenance": "hand_written",
        "note": "a note",
        "prompt_version": "v1",
        "question": "q?",
        "query": {"metric": "obligations"},
        "response": {"verdict": "consistent", "issues": [], "rationale": "ok"},
    }


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"provenance": "model"}, "provenance"),
        ({"extra": 1}, "not a field"),
        ({"query": "obligations"}, "query"),
        ({"response": 3}, "response"),
        ({"note": " "}, "note"),
        ({"question_id": 7}, "question_id"),
    ],
)
def test_a_bad_replay_file_is_refused(change, message) -> None:
    data = {**_valid(), **change}
    with pytest.raises(ReplayFileError, match=message):
        parse_replay(data)


def test_a_missing_replay_field_is_refused() -> None:
    data = _valid()
    del data["prompt_version"]
    with pytest.raises(ReplayFileError, match="prompt_version"):
        parse_replay(data)


def test_two_replays_for_the_same_request_are_refused() -> None:
    first = parse_replay(_valid())
    second = parse_replay({**_valid(), "replay_id": "r2"})
    with pytest.raises(ReplayFileError, match="same question and query"):
        ReplayJudge([first, second])


def test_a_repeated_replay_id_is_refused() -> None:
    first = parse_replay(_valid())
    second = parse_replay({**_valid(), "question": "another?"})
    with pytest.raises(ReplayFileError, match="used twice"):
        ReplayJudge([first, second])


def test_a_replay_directory_with_bad_json_is_refused(tmp_path) -> None:
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ReplayFileError, match="bad.json"):
        load_replays(tmp_path)


def test_a_replay_directory_must_exist(tmp_path) -> None:
    with pytest.raises(ReplayFileError):
        load_replays(tmp_path / "missing")


def test_consistent_replays_carry_their_question_s_expected_query(replays, questions) -> None:
    """A consistent replay must judge the expected query itself, not a near copy of it."""
    for replay in replays.values():
        if replay.replay_id.startswith("consistent") and "null-range" not in replay.replay_id:
            assert replay.query == questions.by_id(replay.question_id).query, replay.replay_id
