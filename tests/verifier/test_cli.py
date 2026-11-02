"""The command line: ``python -m agentic_bi.verifier check``, called through ``main``."""

import io
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from agentic_bi.verifier.__main__ import KEY_ENV, main

QUESTION = "How much was obligated by each sub-agency?"
VALID = {"metric": "obligations", "dimensions": ["sub_agency"], "grain": "total"}
FAKE_KEY = "test-" + "not-a-key"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(KEY_ENV, raising=False)
    monkeypatch.delenv("AGENTIC_BI_VERIFIER_MODEL", raising=False)


def run(query: Any, *extra: str, question: str = QUESTION) -> tuple[int, dict[str, Any], str]:
    text = query if isinstance(query, str) else json.dumps(query)
    out = io.StringIO()
    code = main(["check", "--question", question, "--query", text, *extra], out=out)
    return code, json.loads(out.getvalue()), out.getvalue()


def write_replay(directory: Path, response: Any, name: str = "one") -> None:
    replay = {
        "replay_id": name,
        "provenance": "hand_written",
        "note": "A hand written answer for the command line tests.",
        "prompt_version": "v1",
        "question": QUESTION,
        "query": VALID,
        "response": response,
    }
    (directory / f"{name}.json").write_text(json.dumps(replay), encoding="utf-8")


# --- No key ------------------------------------------------------------------------------


def test_valid_query_without_a_key_is_deterministic_only() -> None:
    code, report, _ = run(VALID)
    assert code == 0
    assert report["deterministic"] == {"passed": True, "issues": []}
    assert report["verdict"] is None
    assert KEY_ENV in report["note"]
    assert "error" not in report
    assert report["question"] == QUESTION
    assert report["query"] == VALID


def test_catalog_failure_prints_a_deterministic_verdict_and_exits_1() -> None:
    code, report, _ = run({"metric": "revenue"})
    assert code == 1
    assert report["deterministic"]["passed"] is False
    assert report["verdict"]["verdict"] == "inconsistent"
    assert report["verdict"]["stage"] == "deterministic"
    assert report["verdict"]["issues"][0]["code"] == "outside_catalog"
    assert report["verdict"]["issues"][0]["refusal_code"] == "unknown_metric"
    assert "no model was called" in report["note"]


def test_slice_failure_prints_refuse_expected_and_exits_1() -> None:
    query = {"metric": "obligations", "time_range": {"start": "2023-01-01", "end": "2023-12-31"}}
    code, report, _ = run(query)
    assert code == 1
    assert report["verdict"]["verdict"] == "refuse_expected"
    assert report["verdict"]["stage"] == "deterministic"
    assert [i["code"] for i in report["verdict"]["issues"]] == ["time_range_outside_slice"]


def test_query_that_is_not_json_prints_invalid_query_and_exits_1() -> None:
    code, report, _ = run("{this is not json")
    assert code == 1
    assert report["query"] == "{this is not json"
    assert report["verdict"]["verdict"] == "inconsistent"
    issue = report["verdict"]["issues"][0]
    assert issue["code"] == "invalid_query"
    assert report["deterministic"]["issues"][0]["explanation"] == "the query is not valid JSON"
    assert issue["explanation"] == "the query is not valid JSON"


def test_query_that_is_a_json_array_is_invalid_query() -> None:
    code, report, _ = run("[1, 2]")
    assert code == 1
    assert report["verdict"]["issues"][0]["code"] == "invalid_query"


def test_empty_question_is_a_run_failure() -> None:
    code, report, _ = run(VALID, question="   ")
    assert code == 2
    assert "VerifierInputError" in report["error"]


# --- A key, but no client library --------------------------------------------------------


def test_key_without_the_client_library_exits_2_and_never_prints_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(KEY_ENV, FAKE_KEY)
    monkeypatch.setitem(sys.modules, "anthropic", None)
    code, report, raw = run(VALID)
    assert code == 2
    assert report["deterministic"]["passed"] is True
    assert report["verdict"] is None
    assert "anthropic" in report["error"]
    assert "client library" in report["error"]
    assert report["note"] == report["error"]
    assert FAKE_KEY not in raw


def test_blank_key_counts_as_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, "   ")
    code, report, _ = run(VALID)
    assert code == 0
    assert KEY_ENV in report["note"]


def test_key_with_a_failing_check_still_decides_without_a_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(KEY_ENV, FAKE_KEY)
    monkeypatch.setitem(sys.modules, "anthropic", None)
    code, report, raw = run({"metric": "revenue"})
    assert code == 1
    assert report["verdict"]["stage"] == "deterministic"
    assert FAKE_KEY not in raw


# --- Replay ------------------------------------------------------------------------------


def test_replay_directory_that_does_not_exist_exits_2(tmp_path: Path) -> None:
    code, report, _ = run(VALID, "--replay", str(tmp_path / "missing"))
    assert code == 2
    assert "ReplayFileError" in report["error"]


def test_replayed_consistent_verdict_exits_0_and_names_the_provenance(tmp_path: Path) -> None:
    write_replay(
        tmp_path, {"verdict": "consistent", "issues": [], "rationale": "It matches the question."}
    )
    code, report, _ = run(VALID, "--replay", str(tmp_path))
    assert code == 0
    assert report["verdict"]["verdict"] == "consistent"
    assert report["verdict"]["stage"] == "model"
    assert report["verdict"]["judge"] == "replay"
    assert report["verdict"]["prompt_version"] == "v1"
    assert "hand_written" in report["note"]
    assert "one" in report["note"]
    assert "not a live model call" in report["note"]


def test_replayed_inconsistent_verdict_exits_1(tmp_path: Path) -> None:
    write_replay(
        tmp_path,
        {
            "verdict": "inconsistent",
            "issues": [{"code": "wrong_grain", "field": "grain", "explanation": "Wants months."}],
            "rationale": "The grain differs.",
        },
    )
    code, report, _ = run(VALID, "--replay", str(tmp_path))
    assert code == 1
    assert report["verdict"]["verdict"] == "inconsistent"
    assert report["verdict"]["issues"][0]["code"] == "wrong_grain"
    assert "hand_written" in report["note"]


def test_replayed_refuse_expected_verdict_exits_1(tmp_path: Path) -> None:
    write_replay(
        tmp_path,
        {
            "verdict": "refuse_expected",
            "issues": [{"code": "should_refuse", "field": None, "explanation": "No such data."}],
            "rationale": "The catalog cannot answer it.",
        },
    )
    code, report, _ = run(VALID, "--replay", str(tmp_path))
    assert code == 1
    assert report["verdict"]["verdict"] == "refuse_expected"


def test_replayed_malformed_answer_exits_2(tmp_path: Path) -> None:
    write_replay(tmp_path, "looks fine to me")
    code, report, _ = run(VALID, "--replay", str(tmp_path))
    assert code == 2
    assert "MalformedVerdictError" in report["error"]


def test_replay_with_no_matching_entry_exits_2(tmp_path: Path) -> None:
    write_replay(tmp_path, {"verdict": "consistent", "issues": [], "rationale": "Fine."})
    other = {"metric": "award_count"}
    code, report, _ = run(other, "--replay", str(tmp_path))
    assert code == 2
    assert "JudgeError" in report["error"]


def test_replay_is_not_used_when_a_check_fails(tmp_path: Path) -> None:
    write_replay(tmp_path, {"verdict": "consistent", "issues": [], "rationale": "Fine."})
    code, report, _ = run({"metric": "revenue"}, "--replay", str(tmp_path))
    assert code == 1
    assert report["verdict"]["stage"] == "deterministic"


def test_replay_wins_over_a_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, FAKE_KEY)
    monkeypatch.setitem(sys.modules, "anthropic", None)
    write_replay(tmp_path, {"verdict": "consistent", "issues": [], "rationale": "Fine."})
    code, report, raw = run(VALID, "--replay", str(tmp_path))
    assert code == 0
    assert report["verdict"]["judge"] == "replay"
    assert FAKE_KEY not in raw


def test_missing_arguments_exit_through_argparse() -> None:
    with pytest.raises(SystemExit) as info:
        main(["check", "--question", QUESTION], out=io.StringIO())
    assert info.value.code == 2
