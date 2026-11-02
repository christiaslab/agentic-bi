"""The model layer: the request shape, the live model's error mapping, the replay and the settings.

Nothing here calls the network or needs a key. The live model gets a stand-in client, the errors it
maps are the SDK's own exception classes built by hand, and the one key-like value (used to prove
that nothing leaks) is built by concatenation.
"""

from __future__ import annotations

import copy
import dataclasses
import inspect
import json
import types
from collections.abc import Callable
from typing import Any

import anthropic
import httpx2
import pytest
from anthropic.types import Message

from agentic_bi.agent.model import (
    REPLAY_MODEL,
    REPLAY_PROVENANCE,
    AnthropicModel,
    ModelError,
    ModelTurn,
    ReplayError,
    ReplayModel,
    build_request,
    content_blocks,
)
from agentic_bi.agent.settings import (
    DEFAULT_MODEL,
    EFFORTS,
    MODEL_ENV,
    THINKING_DISPLAYS,
    AgentSettings,
)
from agentic_bi.agent.tools import tool_definitions

SYSTEM = "You are the system prompt of a test."
SETTINGS = AgentSettings()
TOOLS = [{"name": "catalog", "description": "A tool.", "input_schema": {"type": "object"}}]
REQUEST_KEYS = {
    "model",
    "max_tokens",
    "system",
    "messages",
    "tools",
    "tool_choice",
    "thinking",
    "output_config",
    "cache_control",
}
KEY = "placeholder-" + "not-a-key"
API_REQUEST = httpx2.Request(
    "POST",
    "https://api.anthropic.com/v1/messages",
    headers={"x-api-key": KEY, "authorization": "Bearer " + KEY},
)


# --- build_request: the one place the request is shaped ---------------------------------------


def build(settings: AgentSettings = SETTINGS, **given: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "system": SYSTEM,
        "messages": [{"role": "user", "content": "What were obligations?"}],
        "tools": tool_definitions(),
        "settings": settings,
    }
    return build_request(**(arguments | given))


def test_build_request_takes_the_default_model_from_the_settings() -> None:
    assert build()["model"] == DEFAULT_MODEL == "claude-sonnet-5-5"


def test_build_request_takes_a_custom_model_id_from_the_settings() -> None:
    assert build(AgentSettings(model="custom-model-id"))["model"] == "custom-model-id"


def test_build_request_asks_for_16000_output_tokens_by_default() -> None:
    assert build()["max_tokens"] == 16_000


def test_build_request_takes_max_tokens_from_the_settings() -> None:
    assert build(AgentSettings(max_tokens=4_000))["max_tokens"] == 4_000


def test_build_request_asks_for_adaptive_thinking_with_a_summary() -> None:
    assert build()["thinking"] == {"type": "adaptive", "display": "summarized"}


@pytest.mark.parametrize("display", THINKING_DISPLAYS)
def test_build_request_takes_the_thinking_display_from_the_settings(display: str) -> None:
    sent = build(AgentSettings(thinking_display=display))
    assert sent["thinking"] == {"type": "adaptive", "display": display}


def test_build_request_sends_high_effort_by_default() -> None:
    assert build()["output_config"] == {"effort": "high"}


@pytest.mark.parametrize("effort", EFFORTS)
def test_build_request_takes_the_effort_from_the_settings(effort: str) -> None:
    assert build(AgentSettings(effort=effort))["output_config"] == {"effort": effort}


def test_build_request_leaves_the_choice_of_tool_to_the_model() -> None:
    assert build()["tool_choice"] == {"type": "auto"}


def test_build_request_turns_ephemeral_prompt_caching_on() -> None:
    assert build()["cache_control"] == {"type": "ephemeral"}


CUSTOM = AgentSettings(
    model="custom-model-id",
    max_tokens=1_000,
    effort="max",
    thinking_display="omitted",
    max_model_calls=3,
    max_tool_calls=4,
    max_answer_attempts=1,
    max_rows=5,
    timeout_seconds=2.5,
)


@pytest.mark.parametrize("settings", [SETTINGS, CUSTOM], ids=["default", "custom"])
def test_build_request_has_exactly_the_keys_of_the_adr(settings: AgentSettings) -> None:
    sent = build(settings)
    assert set(sent) == REQUEST_KEYS
    # the fixed parts do not depend on the settings
    assert sent["tool_choice"] == {"type": "auto"}
    assert sent["cache_control"] == {"type": "ephemeral"}


@pytest.mark.parametrize("settings", [SETTINGS, CUSTOM], ids=["default", "custom"])
def test_build_request_sends_no_sampling_parameter_and_no_beta(settings: AgentSettings) -> None:
    sent = build(settings)
    for name in ("temperature", "top_p", "top_k", "betas", "mcp_servers", "container"):
        assert name not in sent


def test_build_request_passes_the_system_prompt_messages_and_tools_through() -> None:
    messages = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": [{"type": "text", "text": "a"}]},
        {"role": "user", "content": "feedback"},
    ]
    tools = tool_definitions()
    sent = build(system="the system text", messages=messages, tools=tools)
    assert sent["system"] == "the system text"
    assert sent["messages"] == messages
    assert sent["tools"] == tools


def test_build_request_carries_the_four_project_tools_and_no_server_tool() -> None:
    tools = build()["tools"]
    assert [tool["name"] for tool in tools] == ["catalog", "schema", "run_query", "explain_measure"]
    # a server tool has a versioned type (such as web_search_20250305) and no input schema
    assert all("input_schema" in tool and tool.get("type", "custom") == "custom" for tool in tools)


def test_build_request_does_not_change_what_it_is_given() -> None:
    messages = [{"role": "user", "content": "q"}]
    tools = tool_definitions()
    system, settings = SYSTEM, AgentSettings(effort="low")
    before = copy.deepcopy((system, messages, tools, settings))
    build(settings, system=system, messages=messages, tools=tools)
    assert (system, messages, tools, settings) == before


def test_build_request_is_plain_json() -> None:
    sent = build(CUSTOM)
    assert json.loads(json.dumps(sent)) == sent


# --- content_blocks: blocks as returned, minus top-level nulls --------------------------------


def test_content_blocks_drops_top_level_none_values_only() -> None:
    blocks = [
        {"type": "text", "text": "hi", "citations": None},
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "run_query",
            "input": {"time_range": None, "filters": [None], "nested": {"a": None}},
        },
    ]
    assert content_blocks(blocks) == [
        {"type": "text", "text": "hi"},
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "run_query",
            "input": {"time_range": None, "filters": [None], "nested": {"a": None}},
        },
    ]


def test_content_blocks_keeps_every_value_that_is_falsy_but_not_none() -> None:
    block = {"type": "text", "text": "", "count": 0, "flag": False, "items": [], "input": {}}
    assert content_blocks([block]) == [block]


def test_content_blocks_returns_thinking_blocks_unchanged() -> None:
    thinking = {"type": "thinking", "thinking": "a summary", "signature": "sig-1"}
    redacted = {"type": "redacted_thinking", "data": "opaque"}
    assert content_blocks([thinking, redacted]) == [thinking, redacted]


def test_content_blocks_keeps_the_order_and_gives_new_dicts() -> None:
    blocks = [{"type": "text", "text": "one"}, {"type": "text", "text": "two"}]
    result = content_blocks(blocks)
    assert [block["text"] for block in result] == ["one", "two"]
    result[0]["text"] = "changed"
    assert blocks[0]["text"] == "one"


def test_content_blocks_of_no_blocks_is_an_empty_list() -> None:
    assert content_blocks([]) == []


def test_content_blocks_accepts_any_mapping() -> None:
    block = types.MappingProxyType({"type": "text", "text": "hi", "citations": None})
    assert content_blocks([block]) == [{"type": "text", "text": "hi"}]


# --- ModelTurn and ModelError -----------------------------------------------------------------


def test_model_turn_to_dict_lists_every_field() -> None:
    turn = ModelTurn(
        content=[{"type": "text", "text": "x"}],
        stop_reason="end_turn",
        model="model-a",
        usage={"input_tokens": 1},
        request_id="req_1",
        stop_details={"category": "cyber"},
    )
    assert turn.to_dict() == {
        "model": "model-a",
        "stop_reason": "end_turn",
        "stop_details": {"category": "cyber"},
        "usage": {"input_tokens": 1},
        "request_id": "req_1",
        "content": [{"type": "text", "text": "x"}],
    }


def test_model_turn_has_no_usage_request_id_or_stop_details_unless_given() -> None:
    turn = ModelTurn(content=[], stop_reason=None, model="model-a")
    assert turn.usage == {} and turn.request_id is None and turn.stop_details is None
    assert ModelTurn(content=[], stop_reason=None, model="model-a").usage is not turn.usage


def test_model_turn_is_frozen() -> None:
    turn = ModelTurn(content=[], stop_reason="end_turn", model="model-a")
    with pytest.raises(dataclasses.FrozenInstanceError):
        turn.model = "model-b"


def test_model_error_says_whether_every_later_call_would_fail_the_same_way() -> None:
    assert ModelError("stop", fatal=True).fatal is True
    assert ModelError("go on", fatal=False).fatal is False
    assert str(ModelError("stop", fatal=True)) == "stop"
    with pytest.raises(TypeError):
        ModelError("no flag")


def test_replay_error_is_an_assertion_error() -> None:
    assert issubclass(ReplayError, AssertionError)


# --- AnthropicModel with a stand-in client ----------------------------------------------------

MESSAGE: dict[str, Any] = {
    "id": "msg_x",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-5-5",
    "content": [
        {"type": "text", "text": "hi", "citations": None},
        {"type": "tool_use", "id": "toolu_1", "name": "catalog", "input": {"time_range": None}},
    ],
    "stop_reason": "tool_use",
    "stop_details": None,
    "usage": {"input_tokens": 10, "output_tokens": 5},
}


class StandInResponse:
    """What the SDK returns: ``to_dict(mode="json")`` and a ``_request_id``."""

    def __init__(self, data: dict[str, Any], request_id: str | None = "req_test_1") -> None:
        self._data = data
        if request_id is not None:
            self._request_id = request_id
        self.modes: list[str] = []

    def to_dict(self, *, mode: str = "python") -> dict[str, Any]:
        self.modes.append(mode)
        return copy.deepcopy(self._data)


class StandInClient:
    """Records every ``messages.create`` call; returns the outcome, or raises it if an error."""

    def __init__(self, outcome: object) -> None:
        self.calls: list[dict[str, Any]] = []
        self._outcome = outcome
        self.messages = types.SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome


def call(client: object, settings: AgentSettings = SETTINGS, **given: Any) -> ModelTurn:
    arguments: dict[str, Any] = {
        "system": SYSTEM,
        "messages": [{"role": "user", "content": "What were obligations?"}],
        "tools": tool_definitions(),
    }
    return AnthropicModel(client).create(**(arguments | given), settings=settings)


def test_anthropic_model_sends_exactly_the_request_that_build_request_shapes() -> None:
    client = StandInClient(StandInResponse(MESSAGE))
    settings = AgentSettings(model="custom-model-id", effort="low")
    messages = [{"role": "user", "content": "q"}]
    tools = tool_definitions()
    call(client, settings, messages=messages, tools=tools)
    expected = build_request(system=SYSTEM, messages=messages, tools=tools, settings=settings)
    assert client.calls == [expected]


def test_anthropic_model_returns_the_response_as_a_model_turn() -> None:
    turn = call(StandInClient(StandInResponse(MESSAGE)))
    assert turn == ModelTurn(
        content=[
            {"type": "text", "text": "hi"},
            {"type": "tool_use", "id": "toolu_1", "name": "catalog", "input": {"time_range": None}},
        ],
        stop_reason="tool_use",
        model="claude-sonnet-5-5",
        usage={"input_tokens": 10, "output_tokens": 5},
        request_id="req_test_1",
        stop_details=None,
    )


def test_anthropic_model_drops_top_level_nulls_but_keeps_nulls_inside_a_tool_input() -> None:
    turn = call(StandInClient(StandInResponse(MESSAGE)))
    assert "citations" not in turn.content[0]
    assert turn.content[1]["input"] == {"time_range": None}


def test_anthropic_model_reads_the_response_in_json_mode() -> None:
    response = StandInResponse(MESSAGE)
    call(StandInClient(response))
    assert response.modes == ["json"]


def test_anthropic_model_records_the_model_that_answered_not_the_one_asked_for() -> None:
    data = MESSAGE | {"model": "served-model-id"}
    settings = AgentSettings(model="custom-model-id")
    turn = call(StandInClient(StandInResponse(data)), settings)
    assert turn.model == "served-model-id"


@pytest.mark.parametrize("named", [None, ""], ids=["none", "empty"])
def test_anthropic_model_falls_back_to_the_settings_model_when_the_response_names_none(
    named: str | None,
) -> None:
    settings = AgentSettings(model="custom-model-id")
    turn = call(StandInClient(StandInResponse(MESSAGE | {"model": named})), settings)
    assert turn.model == "custom-model-id"


def test_anthropic_model_keeps_the_stop_details_as_the_api_sent_them() -> None:
    details = {"type": "refusal", "category": "cyber", "explanation": None}
    data = MESSAGE | {"stop_reason": "refusal", "stop_details": details}
    turn = call(StandInClient(StandInResponse(data)))
    assert turn.stop_reason == "refusal" and turn.stop_details == details


def test_anthropic_model_tolerates_a_response_with_nothing_but_a_stop_reason() -> None:
    data = {"stop_reason": "end_turn", "content": None, "usage": None}
    turn = call(StandInClient(StandInResponse(data, request_id=None)))
    assert turn.content == [] and turn.usage == {} and turn.request_id is None
    assert turn.stop_reason == "end_turn" and turn.stop_details is None
    assert turn.model == SETTINGS.model


def test_anthropic_model_makes_one_call_per_turn() -> None:
    client = StandInClient(StandInResponse(MESSAGE))
    call(client)
    assert len(client.calls) == 1


def test_anthropic_model_reads_the_sdks_own_message_object() -> None:
    message = Message.model_validate(
        MESSAGE
        | {
            "stop_sequence": None,
            "content": [
                {"type": "thinking", "thinking": "a summary", "signature": "sig-1"},
                *MESSAGE["content"],
            ],
        }
    )
    message._request_id = "req_sdk_1"
    turn = call(StandInClient(message))
    assert turn.content == [
        {"type": "thinking", "thinking": "a summary", "signature": "sig-1"},
        {"type": "text", "text": "hi"},
        {"type": "tool_use", "id": "toolu_1", "name": "catalog", "input": {"time_range": None}},
    ]
    assert (turn.stop_reason, turn.model, turn.request_id) == (
        "tool_use",
        "claude-sonnet-5-5",
        "req_sdk_1",
    )
    assert turn.usage == {"input_tokens": 10, "output_tokens": 5}


def test_anthropic_model_given_a_client_builds_no_client_of_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the live client must not be built")

    monkeypatch.setattr(anthropic, "Anthropic", refuse)
    client = StandInClient(StandInResponse(MESSAGE))
    call(client)
    assert len(client.calls) == 1


def test_anthropic_model_leaves_the_key_to_the_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    class Recorder:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            built.append((args, kwargs))

    monkeypatch.setattr(anthropic, "Anthropic", Recorder)
    AnthropicModel()
    assert built == [((), {})]


# --- the SDK's errors, mapped without leaking anything ----------------------------------------


def status_error(
    error_class: type[anthropic.APIStatusError], status: int, message: str = "the API said no"
) -> anthropic.APIStatusError:
    response = httpx2.Response(status, request=API_REQUEST, headers={"request-id": "req_failed"})
    return error_class(message, response=response, body=None)


def raised_model_error(error: Exception, settings: AgentSettings = SETTINGS) -> ModelError:
    with pytest.raises(ModelError) as caught:
        call(StandInClient(error), settings)
    assert caught.value.__cause__ is error
    return caught.value


STATUS_ERRORS = [
    pytest.param(anthropic.BadRequestError, 400, True, id="400-bad-request"),
    pytest.param(anthropic.AuthenticationError, 401, True, id="401-authentication"),
    pytest.param(anthropic.PermissionDeniedError, 403, True, id="403-permission-denied"),
    pytest.param(anthropic.NotFoundError, 404, True, id="404-not-found"),
    pytest.param(anthropic.RateLimitError, 429, False, id="429-rate-limit"),
    pytest.param(anthropic.InternalServerError, 500, False, id="500-internal-server"),
    pytest.param(anthropic.ServiceUnavailableError, 503, False, id="503-unavailable"),
    pytest.param(anthropic.DeadlineExceededError, 504, False, id="504-deadline"),
    pytest.param(anthropic.OverloadedError, 529, False, id="529-overloaded"),
]


@pytest.mark.parametrize(("error_class", "status", "fatal"), STATUS_ERRORS)
def test_an_api_status_error_is_fatal_only_when_every_later_call_would_fail_too(
    error_class: type[anthropic.APIStatusError], status: int, fatal: bool
) -> None:
    assert raised_model_error(status_error(error_class, status)).fatal is fatal


CONNECTION_ERRORS = [
    pytest.param(lambda: anthropic.APIConnectionError(request=API_REQUEST), id="connection"),
    pytest.param(lambda: anthropic.APITimeoutError(request=API_REQUEST), id="timeout"),
]


@pytest.mark.parametrize("make", CONNECTION_ERRORS)
def test_a_connection_error_is_not_fatal(make: Callable[[], Exception]) -> None:
    model_error = raised_model_error(make())
    assert model_error.fatal is False
    assert "could not reach the API" in str(model_error)


def test_a_refused_key_is_fatal_and_the_message_never_echoes_what_the_api_said() -> None:
    error = status_error(anthropic.AuthenticationError, 401, "invalid x-api-key: " + KEY)
    model_error = raised_model_error(error)
    assert model_error.fatal is True
    assert "ANTHROPIC_API_KEY" in str(model_error)
    assert KEY not in str(model_error)


def test_a_missing_model_is_fatal_and_the_message_names_the_model_id() -> None:
    error = status_error(anthropic.NotFoundError, 404, "not found")
    model_error = raised_model_error(error, AgentSettings(model="custom-model-id"))
    assert model_error.fatal is True
    assert "custom-model-id" in str(model_error)


def test_a_refused_request_is_fatal_and_the_message_carries_the_apis_reason() -> None:
    error = status_error(anthropic.BadRequestError, 400, "max_tokens: too large for the model")
    model_error = raised_model_error(error)
    assert model_error.fatal is True
    assert "max_tokens: too large for the model" in str(model_error)


def test_a_rate_limit_is_not_fatal_and_says_the_sdk_already_retried() -> None:
    model_error = raised_model_error(status_error(anthropic.RateLimitError, 429))
    assert model_error.fatal is False
    assert "rate limited" in str(model_error)


def test_any_other_status_error_names_its_status_and_is_not_fatal() -> None:
    model_error = raised_model_error(status_error(anthropic.InternalServerError, 500, "oops"))
    assert model_error.fatal is False
    assert "API error 500" in str(model_error) and "oops" in str(model_error)


def assert_no_secret_in(model_error: ModelError) -> None:
    text = f"{model_error} {model_error!r} {model_error.args}".lower()
    assert KEY not in text
    for header in ("x-api-key", "authorization", "bearer"):
        assert header not in text


@pytest.mark.parametrize(
    ("error_class", "status"),
    [pytest.param(*case.values[:2], id=case.id) for case in STATUS_ERRORS],
)
def test_a_model_error_from_a_status_error_never_holds_the_request_headers_or_a_key(
    error_class: type[anthropic.APIStatusError], status: int
) -> None:
    # the request that failed carried the key in its headers; the error must not
    assert_no_secret_in(raised_model_error(status_error(error_class, status)))


@pytest.mark.parametrize("make", CONNECTION_ERRORS)
def test_a_model_error_from_a_connection_error_never_holds_the_request_headers_or_a_key(
    make: Callable[[], Exception],
) -> None:
    assert_no_secret_in(raised_model_error(make()))


def test_an_error_that_is_not_the_sdks_passes_through_unchanged() -> None:
    error = ValueError("not from the SDK")
    with pytest.raises(ValueError) as caught:
        call(StandInClient(error))
    assert caught.value is error
    assert not isinstance(caught.value, ModelError)


def test_an_error_that_is_not_the_sdks_keeps_the_cause_it_came_with() -> None:
    original = KeyError("the real cause")
    error = ValueError("not from the SDK")
    error.__cause__ = original
    with pytest.raises(ValueError) as caught:
        call(StandInClient(error))
    assert caught.value is error
    assert caught.value.__cause__ is original


# --- ReplayModel ------------------------------------------------------------------------------

THINKING = {"type": "thinking", "thinking": "a step", "signature": "sig-1"}
CALL = {"type": "tool_use", "id": "toolu_1", "name": "catalog", "input": {}}


def tool_turn(*calls: tuple[str, str, dict[str, Any]]) -> dict[str, Any]:
    blocks = [{"type": "tool_use", "id": i, "name": n, "input": p} for i, n, p in calls]
    return {"stop_reason": "tool_use", "content": blocks}


def text_turn(text: str = "done") -> dict[str, Any]:
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}


def user(content: Any) -> dict[str, Any]:
    return {"role": "user", "content": content}


def assistant(content: Any) -> dict[str, Any]:
    return {"role": "assistant", "content": content}


def results(*ids: str) -> list[dict[str, Any]]:
    return [{"type": "tool_result", "tool_use_id": i, "content": "data"} for i in ids]


def step(replay: ReplayModel, messages: list[dict[str, Any]]) -> ModelTurn:
    return replay.create(system=SYSTEM, messages=messages, tools=TOOLS, settings=SETTINGS)


def test_replay_needs_at_least_one_turn() -> None:
    with pytest.raises(ReplayError, match="at least one turn"):
        ReplayModel([])


BAD_TURNS = [
    pytest.param({"stop_reason": "end_turn", "content": [], "usage": {}}, id="extra-key"),
    pytest.param({"stop_reason": "end_turn"}, id="no-content"),
    pytest.param({"content": []}, id="no-stop-reason"),
    pytest.param({"stop_reason": "end_turn", "content": "text"}, id="content-is-text"),
    pytest.param({"stop_reason": "end_turn", "content": None}, id="content-is-none"),
]


@pytest.mark.parametrize("bad", BAD_TURNS)
def test_replay_refuses_a_turn_that_is_not_exactly_stop_reason_and_content(
    bad: dict[str, Any],
) -> None:
    with pytest.raises(ReplayError, match="turn 2 must have exactly stop_reason and content"):
        ReplayModel([text_turn(), bad])


def test_replay_returns_its_turns_in_order() -> None:
    replay = ReplayModel([tool_turn(("toolu_1", "catalog", {})), text_turn("done")])
    first = step(replay, [user("q")])
    assert first.stop_reason == "tool_use"
    assert first.content == [CALL]
    second = step(replay, [user("q"), assistant(first.content), user(results("toolu_1"))])
    assert second.stop_reason == "end_turn"
    assert second.content == [{"type": "text", "text": "done"}]


def test_replay_turns_name_the_replay_model_and_report_no_usage() -> None:
    turn = step(ReplayModel([text_turn()]), [user("q")])
    assert turn.model == REPLAY_MODEL
    assert turn.usage == {} and turn.request_id is None and turn.stop_details is None


def test_replay_drops_top_level_nulls_like_the_live_model() -> None:
    written = {
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "x", "citations": None}],
    }
    replay = ReplayModel([written, text_turn("again")])
    first = step(replay, [user("q")])
    assert first.content == [{"type": "text", "text": "x"}]
    second = step(replay, [user("q"), assistant(first.content), user("feedback")])
    assert second.content == [{"type": "text", "text": "again"}]


def test_replay_hands_out_copies_so_a_changed_turn_is_caught() -> None:
    replay = ReplayModel([tool_turn(("toolu_1", "catalog", {})), text_turn()])
    first = step(replay, [user("q")])
    first.content[0]["input"]["changed"] = True
    messages = [user("q"), assistant(first.content), user(results("toolu_1"))]
    with pytest.raises(ReplayError, match="did not go back into the history unchanged"):
        step(replay, messages)


def test_replay_copies_the_turns_it_is_given() -> None:
    turns = [text_turn("as written")]
    replay = ReplayModel(turns)
    turns[0]["content"][0]["text"] = "changed afterwards"
    assert step(replay, [user("q")]).content == [{"type": "text", "text": "as written"}]


def test_replay_records_every_request_as_a_deep_copy() -> None:
    replay = ReplayModel([tool_turn(("toolu_1", "catalog", {})), text_turn()])
    messages = [user("q")]
    first = step(replay, messages)
    # the loop appends to the same list between calls
    messages += [assistant(first.content), user(results("toolu_1"))]
    step(replay, messages)
    assert [len(request["messages"]) for request in replay.requests] == [1, 3]
    messages[0]["content"] = "changed later"
    assert replay.requests[0]["messages"][0]["content"] == "q"


def test_replay_records_the_request_the_live_model_would_send() -> None:
    replay = ReplayModel([text_turn()])
    settings = AgentSettings(model="custom-model-id", effort="low")
    messages = [user("q")]
    replay.create(system=SYSTEM, messages=messages, tools=TOOLS, settings=settings)
    expected = build_request(system=SYSTEM, messages=messages, tools=TOOLS, settings=settings)
    assert replay.requests == [expected]


def test_replay_raises_when_the_loop_asks_for_more_turns_than_it_has() -> None:
    replay = ReplayModel([text_turn("only")])
    first = step(replay, [user("q")])
    with pytest.raises(ReplayError, match="asked for turn 2; the replay has 1"):
        step(replay, [user("q"), assistant(first.content), user("feedback")])
    assert len(replay.requests) == 1


@pytest.mark.parametrize(
    "messages",
    [
        pytest.param([user("q"), user("again")], id="two-user-messages"),
        pytest.param([user("q"), assistant([CALL]), user(results("toolu_1"))], id="a-history"),
    ],
)
def test_replay_raises_when_the_first_request_holds_more_than_the_question(
    messages: list[dict[str, Any]],
) -> None:
    with pytest.raises(ReplayError, match="the first request must hold the question only"):
        step(ReplayModel([text_turn()]), messages)


@pytest.mark.parametrize(
    "messages",
    [
        pytest.param([], id="empty"),
        pytest.param([assistant([CALL])], id="assistant-first"),
    ],
)
def test_replay_raises_when_the_conversation_does_not_open_with_the_users_question(
    messages: list[dict[str, Any]],
) -> None:
    with pytest.raises(ReplayError, match="must open with the user's question"):
        step(ReplayModel([text_turn()]), messages)


CALLING_THINKING = {"stop_reason": "tool_use", "content": [THINKING, CALL]}
CHANGED_HISTORY = [
    pytest.param(assistant([CALL]), id="thinking-block-dropped"),
    pytest.param(assistant([{**THINKING, "signature": "other"}, CALL]), id="signature-changed"),
    pytest.param(assistant([THINKING, {**CALL, "input": {"extra": 1}}]), id="input-changed"),
    pytest.param(assistant([THINKING, CALL, {"type": "text", "text": "x"}]), id="block-added"),
    pytest.param(user([THINKING, CALL]), id="sent-as-a-user-message"),
    pytest.param(assistant("text only"), id="content-is-text"),
]


@pytest.mark.parametrize("sent_back", CHANGED_HISTORY)
def test_replay_raises_when_the_previous_assistant_turn_did_not_go_back_unchanged(
    sent_back: dict[str, Any],
) -> None:
    replay = ReplayModel([CALLING_THINKING, text_turn()])
    step(replay, [user("q")])
    messages = [user("q"), sent_back, user(results("toolu_1"))]
    with pytest.raises(ReplayError, match="turn 1 did not go back into the history unchanged"):
        step(replay, messages)


def test_replay_treats_a_request_that_holds_only_a_question_as_the_next_conversation() -> None:
    replay = ReplayModel([text_turn("one"), text_turn("two")])
    step(replay, [user("first question")])
    second = step(replay, [user("second question")])
    assert second.content == [{"type": "text", "text": "two"}]
    assert [request["messages"] for request in replay.requests] == [
        [user("first question")],
        [user("second question")],
    ]


@pytest.mark.parametrize(
    "history",
    [
        pytest.param(lambda first: [user("q"), user(results("toolu_1"))], id="no-assistant-turn"),
        pytest.param(lambda first: [user("q"), assistant(first.content)], id="no-user-message"),
    ],
)
def test_replay_raises_a_replay_error_when_the_history_is_too_short_for_the_previous_turn(
    history: Callable[[ModelTurn], list[dict[str, Any]]],
) -> None:
    replay = ReplayModel([tool_turn(("toolu_1", "catalog", {})), text_turn()])
    first = step(replay, [user("q")])
    with pytest.raises(ReplayError):
        step(replay, history(first))


TWO_CALLS = tool_turn(("t1", "catalog", {}), ("t2", "schema", {"dimension": "psc"}))
WRONG_RESULTS = [
    pytest.param(results("t2", "t1"), id="reversed"),
    pytest.param(results("t1"), id="one-missing"),
    pytest.param(results("t1", "t2", "t3"), id="one-extra"),
    pytest.param(results("t1", "other"), id="unknown-id"),
    pytest.param(results("t1", "t2") + [{"type": "text", "text": "x"}], id="extra-text-block"),
    pytest.param(
        [{"type": "text", "tool_use_id": "t1"}, {"type": "text", "tool_use_id": "t2"}],
        id="not-tool-results",
    ),
    pytest.param("here are the results", id="plain-text"),
]


@pytest.mark.parametrize("content", WRONG_RESULTS)
def test_replay_raises_when_the_tool_results_do_not_answer_the_calls_in_order(
    content: Any,
) -> None:
    replay = ReplayModel([TWO_CALLS, text_turn()])
    first = step(replay, [user("q")])
    with pytest.raises(ReplayError, match=r"request 2 must answer tool calls \['t1', 't2'\]"):
        step(replay, [user("q"), assistant(first.content), user(content)])


def test_replay_accepts_the_results_of_several_calls_in_order_in_one_message() -> None:
    replay = ReplayModel([TWO_CALLS, text_turn()])
    first = step(replay, [user("q")])
    last = step(replay, [user("q"), assistant(first.content), user(results("t1", "t2"))])
    assert last.stop_reason == "end_turn"


def test_replay_accepts_the_loops_feedback_after_a_final_reply() -> None:
    replay = ReplayModel([text_turn("not json"), text_turn("second try")])
    first = step(replay, [user("q")])
    feedback = user("[harness check] your reply was not accepted")
    second = step(replay, [user("q"), assistant(first.content), feedback])
    assert second.content == [{"type": "text", "text": "second try"}]


def test_replay_raises_when_a_later_request_does_not_end_with_a_user_message() -> None:
    replay = ReplayModel([text_turn("one"), text_turn("two")])
    first = step(replay, [user("q")])
    messages = [user("q"), assistant(first.content), assistant(first.content)]
    with pytest.raises(ReplayError, match="request 2 must end with a user message"):
        step(replay, messages)


def test_replay_is_exhausted_only_after_its_last_turn() -> None:
    replay = ReplayModel([tool_turn(("toolu_1", "catalog", {})), text_turn()])
    assert replay.exhausted is False
    first = step(replay, [user("q")])
    assert replay.exhausted is False
    step(replay, [user("q"), assistant(first.content), user(results("toolu_1"))])
    assert replay.exhausted is True


def test_replay_says_its_turns_are_hand_written() -> None:
    replay = ReplayModel([text_turn()])
    assert replay.model == REPLAY_MODEL and replay.provenance == REPLAY_PROVENANCE
    assert "hand-written" in REPLAY_MODEL and "not model output" in REPLAY_MODEL
    assert "hand-written" in REPLAY_PROVENANCE and "no model produced them" in REPLAY_PROVENANCE
    assert REPLAY_PROVENANCE.startswith("replay")


def test_replay_model_and_provenance_can_be_given() -> None:
    replay = ReplayModel([text_turn()], model="test-model", provenance="test: prepared turns")
    assert (replay.model, replay.provenance) == ("test-model", "test: prepared turns")
    assert step(replay, [user("q")]).model == "test-model"


def test_both_models_take_the_same_keyword_only_arguments() -> None:
    expected = ["system", "messages", "tools", "settings"]
    for model_class in (AnthropicModel, ReplayModel):
        parameters = list(inspect.signature(model_class.create).parameters.values())[1:]
        assert [parameter.name for parameter in parameters] == expected
        assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in parameters)


# --- AgentSettings ----------------------------------------------------------------------------


def test_settings_defaults() -> None:
    settings = AgentSettings()
    assert settings.model == DEFAULT_MODEL == "claude-sonnet-5-5"
    assert settings.max_tokens == 16_000
    assert settings.effort == "high"
    assert settings.thinking_display == "summarized"
    assert settings.max_model_calls == 12
    assert settings.max_tool_calls == 16
    assert settings.max_answer_attempts == 2
    assert settings.max_rows == 200
    assert settings.timeout_seconds == 30.0


def test_settings_are_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        AgentSettings().model = "other"


def test_the_model_variable_is_agentic_bi_model() -> None:
    assert MODEL_ENV == "AGENTIC_BI_MODEL"


def test_from_env_without_the_variable_gives_the_defaults() -> None:
    assert AgentSettings.from_env({}) == AgentSettings()


def test_from_env_reads_the_model_from_the_variable() -> None:
    assert AgentSettings.from_env({MODEL_ENV: "env-model-id"}).model == "env-model-id"


def test_from_env_strips_whitespace_around_the_model() -> None:
    assert AgentSettings.from_env({MODEL_ENV: "  env-model-id \n"}).model == "env-model-id"


@pytest.mark.parametrize("value", ["", "   ", "\t\n"], ids=["empty", "spaces", "whitespace"])
def test_from_env_ignores_an_empty_model_variable(value: str) -> None:
    assert AgentSettings.from_env({MODEL_ENV: value}).model == DEFAULT_MODEL


def test_from_env_reads_the_process_environment_when_none_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert AgentSettings.from_env().model == DEFAULT_MODEL
    monkeypatch.setenv(MODEL_ENV, "process-model-id")
    assert AgentSettings.from_env().model == "process-model-id"


def test_an_explicit_override_beats_the_environment() -> None:
    settings = AgentSettings.from_env({MODEL_ENV: "env-model-id"}, model="flag-model-id")
    assert settings.model == "flag-model-id"


def test_overrides_that_are_none_are_ignored() -> None:
    settings = AgentSettings.from_env(
        {MODEL_ENV: "env-model-id"}, model=None, effort=None, max_rows=None
    )
    assert settings.model == "env-model-id"
    assert settings.effort == "high" and settings.max_rows == 200


def test_overrides_set_the_other_fields() -> None:
    settings = AgentSettings.from_env({}, effort="low", max_rows=50, timeout_seconds=5)
    assert (settings.effort, settings.max_rows, settings.timeout_seconds) == ("low", 50, 5)


def test_an_unknown_override_raises_a_type_error_naming_it() -> None:
    with pytest.raises(TypeError, match="unknown settings: bogus"):
        AgentSettings.from_env({}, bogus=1)
    with pytest.raises(TypeError, match="unknown settings: alpha, bogus"):
        AgentSettings.from_env({}, bogus=1, alpha=2)


def test_an_invalid_override_raises_a_value_error() -> None:
    with pytest.raises(ValueError, match="effort must be one of"):
        AgentSettings.from_env({MODEL_ENV: "env-model-id"}, effort="turbo")


@pytest.mark.parametrize("value", ["", "   ", None, 5], ids=repr)
def test_the_model_must_be_a_non_empty_text(value: Any) -> None:
    with pytest.raises(ValueError, match="model must be a non-empty model id"):
        AgentSettings(model=value)


@pytest.mark.parametrize("value", ["turbo", "", "High", None], ids=repr)
def test_the_effort_must_be_one_the_api_knows(value: Any) -> None:
    with pytest.raises(ValueError, match="effort must be one of") as caught:
        AgentSettings(effort=value)
    assert all(effort in str(caught.value) for effort in EFFORTS)


@pytest.mark.parametrize("value", ["full", "", "Summarized", None], ids=repr)
def test_the_thinking_display_must_be_summarized_or_omitted(value: Any) -> None:
    assert THINKING_DISPLAYS == ("summarized", "omitted")
    with pytest.raises(ValueError, match="thinking_display must be one of"):
        AgentSettings(thinking_display=value)


COUNTS = ["max_tokens", "max_model_calls", "max_tool_calls", "max_answer_attempts"]


@pytest.mark.parametrize("field", COUNTS)
@pytest.mark.parametrize("value", [0, -1, True, 2.0, "3", None], ids=repr)
def test_a_count_must_be_a_positive_integer(field: str, value: Any) -> None:
    with pytest.raises(ValueError, match=f"{field} must be a positive integer"):
        AgentSettings(**{field: value})


@pytest.mark.parametrize("value", [0, -1, 10_001, True, 5.0, "5", None], ids=repr)
def test_max_rows_must_be_an_integer_from_1_to_10000(value: Any) -> None:
    with pytest.raises(ValueError, match="max_rows must be an integer from 1 to 10000"):
        AgentSettings(max_rows=value)


@pytest.mark.parametrize("value", [0, 0.0, -1, -1.5], ids=repr)
def test_the_timeout_must_be_positive(value: float) -> None:
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        AgentSettings(timeout_seconds=value)


@pytest.mark.parametrize(
    "fields",
    [
        {"max_rows": 1},
        {"max_rows": 10_000},
        {"max_tool_calls": 1, "max_model_calls": 1, "max_answer_attempts": 1, "max_tokens": 1},
        {"timeout_seconds": 0.001},
        {"timeout_seconds": 5},
        {"model": "a"},
    ],
    ids=repr,
)
def test_the_edges_of_the_valid_range_are_accepted(fields: dict[str, Any]) -> None:
    settings = AgentSettings(**fields)
    assert all(getattr(settings, name) == value for name, value in fields.items())


def test_settings_to_dict_lists_every_field_for_the_transcript() -> None:
    assert AgentSettings().to_dict() == {
        "model": "claude-sonnet-5-5",
        "max_tokens": 16_000,
        "effort": "high",
        "thinking_display": "summarized",
        "max_model_calls": 12,
        "max_tool_calls": 16,
        "max_answer_attempts": 2,
        "max_rows": 200,
        "timeout_seconds": 30.0,
    }
    data = CUSTOM.to_dict()
    names = [field.name for field in dataclasses.fields(AgentSettings)]
    assert list(data) == names
    assert data == {name: getattr(CUSTOM, name) for name in names}
    assert json.loads(json.dumps(data)) == data
