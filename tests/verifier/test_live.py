"""The live judge with a fake client: no key, no network, no client library."""

import json
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from agentic_bi.verifier import AnthropicJudge, JudgeError, LiveJudgeUnavailable
from agentic_bi.verifier.judge import DEFAULT_MODEL, MODEL_ENV, JudgeRequest
from agentic_bi.verifier.live import EFFORT, MAX_TOKENS
from agentic_bi.verifier.prompt import PROMPT_VERSION
from agentic_bi.verifier.verdict import MalformedVerdictError
from agentic_bi.verifier.verify import verify

QUERY = {"metric": "obligations", "dimensions": ["sub_agency"], "grain": "total"}
QUESTION = "How much was obligated by each sub-agency?"
CONSISTENT = json.dumps({"verdict": "consistent", "issues": [], "rationale": "It matches."})


def text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def thinking_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="thinking", thinking=text, text=text)


class FakeMessages:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.response


class FakeClient:
    def __init__(self, content: list[Any] | None = None, stop_reason: str = "end_turn") -> None:
        blocks = [text_block(CONSISTENT)] if content is None else content
        self.messages = FakeMessages(SimpleNamespace(stop_reason=stop_reason, content=blocks))


def request() -> JudgeRequest:
    return JudgeRequest(
        system="SYSTEM PROMPT",
        user="USER MESSAGE",
        question=QUESTION,
        query=QUERY,
        prompt_version=PROMPT_VERSION,
    )


@pytest.fixture(autouse=True)
def no_model_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MODEL_ENV, raising=False)


def test_judge_sends_model_system_one_user_message_max_tokens_and_effort() -> None:
    client = FakeClient()
    judge = AnthropicJudge(client=client, model="claude-test-1")
    assert judge.judge(request()) == CONSISTENT
    assert len(client.messages.calls) == 1
    assert client.messages.calls[0] == {
        "model": "claude-test-1",
        "max_tokens": MAX_TOKENS,
        "system": "SYSTEM PROMPT",
        "messages": [{"role": "user", "content": "USER MESSAGE"}],
        "output_config": {"effort": EFFORT},
    }
    assert judge.name == "claude-test-1"


def test_default_model_is_the_pinned_default() -> None:
    assert DEFAULT_MODEL == "claude-haiku-5-5"
    judge = AnthropicJudge(client=FakeClient())
    assert judge.model == DEFAULT_MODEL
    assert judge.name == DEFAULT_MODEL
    client = FakeClient()
    AnthropicJudge(client=client).judge(request())
    assert client.messages.calls[0]["model"] == DEFAULT_MODEL


def test_env_var_overrides_the_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MODEL_ENV, "claude-from-env")
    client = FakeClient()
    judge = AnthropicJudge(client=client)
    judge.judge(request())
    assert judge.name == "claude-from-env"
    assert client.messages.calls[0]["model"] == "claude-from-env"


def test_blank_env_var_falls_back_to_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MODEL_ENV, "   ")
    assert AnthropicJudge(client=FakeClient()).model == DEFAULT_MODEL


def test_explicit_model_beats_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MODEL_ENV, "claude-from-env")
    assert AnthropicJudge(client=FakeClient(), model="claude-explicit").model == "claude-explicit"


def test_text_blocks_are_joined_and_thinking_blocks_skipped() -> None:
    client = FakeClient(
        content=[thinking_block("hidden reasoning"), text_block('{"a": '), text_block("1}")]
    )
    text = AnthropicJudge(client=client).judge(request())
    assert text == '{"a": 1}'
    assert "hidden" not in text


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_unusable_stop_reason_raises_judge_error(stop_reason: str) -> None:
    client = FakeClient(stop_reason=stop_reason)
    with pytest.raises(JudgeError):
        AnthropicJudge(client=client).judge(request())


@pytest.mark.parametrize(
    "content",
    [[], [text_block("")], [text_block("  \n ")], [thinking_block("only thinking")]],
    ids=["no_blocks", "empty_text", "blank_text", "only_thinking"],
)
def test_empty_text_raises_judge_error(content: list[Any]) -> None:
    with pytest.raises(JudgeError, match="no text"):
        AnthropicJudge(client=FakeClient(content=content)).judge(request())


def test_missing_client_library_raises_live_judge_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(LiveJudgeUnavailable, match="anthropic"):
        AnthropicJudge()


def test_a_given_client_needs_no_client_library(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "anthropic", None)
    assert AnthropicJudge(client=FakeClient()).judge(request()) == CONSISTENT


# --- verify() with the live judge --------------------------------------------------------


def test_verify_returns_a_model_verdict_end_to_end() -> None:
    client = FakeClient()
    verification = verify(QUESTION, QUERY, judge=AnthropicJudge(client=client, model="m-1"))
    assert verification.judged
    assert verification.verdict is not None
    assert verification.verdict.verdict == "consistent"
    assert verification.verdict.stage == "model"
    assert verification.verdict.judge == "m-1"
    assert verification.verdict.prompt_version == PROMPT_VERSION
    call = client.messages.calls[0]
    assert call["system"].startswith("prompt_version: v1")
    (message,) = call["messages"]
    assert message["role"] == "user"
    assert QUESTION in message["content"]
    assert "<structured_query>" in message["content"]


def test_verify_with_an_inconsistent_model_answer() -> None:
    answer = {
        "verdict": "inconsistent",
        "issues": [{"code": "wrong_dimension", "field": "dimensions", "explanation": "Extra."}],
        "rationale": "The grouping differs.",
    }
    client = FakeClient(content=[text_block(json.dumps(answer))])
    verification = verify(QUESTION, QUERY, judge=AnthropicJudge(client=client))
    assert verification.verdict is not None
    assert verification.verdict.codes == ("wrong_dimension",)


def test_verify_raises_on_malformed_model_text() -> None:
    client = FakeClient(content=[text_block("The query looks fine to me.")])
    with pytest.raises(MalformedVerdictError):
        verify(QUESTION, QUERY, judge=AnthropicJudge(client=client))
    assert len(client.messages.calls) == 1


def test_verify_never_calls_the_client_when_the_checks_fail() -> None:
    client = FakeClient()
    bad_catalog = {"metric": "revenue"}
    verification = verify(QUESTION, bad_catalog, judge=AnthropicJudge(client=client))
    assert verification.verdict is not None
    assert verification.verdict.stage == "deterministic"
    assert verification.verdict.verdict == "inconsistent"
    out_of_slice = {
        "metric": "obligations",
        "time_range": {"start": "2020-01-01", "end": "2020-02-01"},
    }
    verification = verify(QUESTION, out_of_slice, judge=AnthropicJudge(client=client))
    assert verification.verdict is not None
    assert verification.verdict.verdict == "refuse_expected"
    assert client.messages.calls == []


def test_verify_without_a_judge_leaves_a_passing_query_unjudged() -> None:
    verification = verify(QUESTION, QUERY)
    assert verification.checks.passed
    assert verification.verdict is None
    assert not verification.judged
