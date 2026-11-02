"""The model behind the loop: the Anthropic Messages API live, or hand-written turns offline.

Both implement ``ModelClient.create``: given the system prompt, the conversation so far and the
tool definitions, return the model's next turn as plain JSON blocks (``ModelTurn``). The loop
never sees an SDK object, so a replay drives exactly the code a live run drives.

``build_request`` is the one place the request is shaped (ADR-0005): the model id from the
settings, adaptive thinking with summarized display, the effort from the settings, automatic
tool choice (forced tool use is refused by the current models), prompt caching, and no
sampling parameters (API version 2026-10-21 refuses them). There are no server tools.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from .settings import AgentSettings

REPLAY_MODEL = "replay (hand-written turns, not model output)"
REPLAY_PROVENANCE = (
    "replay: hand-written turns replayed offline to drive the real tool layer; "
    "no model produced them"
)


@dataclass(frozen=True)
class ModelTurn:
    """One response of the model, as JSON blocks."""

    content: list[dict[str, Any]]
    stop_reason: str | None
    model: str
    usage: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    stop_details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "stop_reason": self.stop_reason,
            "stop_details": self.stop_details,
            "usage": self.usage,
            "request_id": self.request_id,
            "content": self.content,
        }


class ModelClient(Protocol):
    def create(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        settings: AgentSettings,
    ) -> ModelTurn: ...


class ModelError(Exception):
    """A model call failed. ``fatal`` means every later call would fail the same way."""

    def __init__(self, message: str, *, fatal: bool) -> None:
        super().__init__(message)
        self.fatal = fatal


class ReplayError(AssertionError):
    """A replay does not fit the conversation the loop built: a broken replay or loop."""


def build_request(
    *,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    settings: AgentSettings,
) -> dict[str, Any]:
    """The keyword arguments of one ``messages.create`` call."""
    return {
        "model": settings.model,
        "max_tokens": settings.max_tokens,
        "system": system,
        "messages": messages,
        "tools": tools,
        "tool_choice": {"type": "auto"},
        "thinking": {"type": "adaptive", "display": settings.thinking_display},
        "output_config": {"effort": settings.effort},
        "cache_control": {"type": "ephemeral"},
    }


def content_blocks(blocks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Response blocks ready to go back in the history: as returned, minus top-level nulls.

    The history is append-only and each block goes back unchanged (thinking blocks included),
    except that a key the API sent as null is dropped; values inside a tool input are kept.
    """
    return [{key: value for key, value in block.items() if value is not None} for block in blocks]


class AnthropicModel:
    """The live model. The SDK reads ``ANTHROPIC_API_KEY`` itself; this class never sees it."""

    def __init__(self, client: Any | None = None) -> None:
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self._client = client

    def create(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        settings: AgentSettings,
    ) -> ModelTurn:
        request = build_request(system=system, messages=messages, tools=tools, settings=settings)
        try:
            response = self._client.messages.create(**request)
        except Exception as exc:  # the SDK's errors, mapped without leaking anything else
            raise _model_error(exc, settings) from exc
        data = response.to_dict(mode="json")
        return ModelTurn(
            content=content_blocks(data.get("content") or []),
            stop_reason=data.get("stop_reason"),
            model=str(data.get("model") or settings.model),
            usage=dict(data.get("usage") or {}),
            request_id=getattr(response, "_request_id", None),
            stop_details=data.get("stop_details"),
        )


def _model_error(exc: Exception, settings: AgentSettings) -> Exception:
    try:
        import anthropic
    except ImportError:  # pragma: no cover - the package is a dependency
        return exc
    if isinstance(exc, anthropic.AuthenticationError):
        return ModelError("the API refused the key: check ANTHROPIC_API_KEY", fatal=True)
    if isinstance(exc, anthropic.PermissionDeniedError):
        return ModelError("the key may not use this model or endpoint", fatal=True)
    if isinstance(exc, anthropic.NotFoundError):
        return ModelError(f"model {settings.model!r} was not found; check --model", fatal=True)
    if isinstance(exc, anthropic.BadRequestError):
        return ModelError(f"the API refused the request: {exc.message}", fatal=True)
    if isinstance(exc, anthropic.RateLimitError):
        return ModelError("rate limited after the SDK's retries", fatal=False)
    if isinstance(exc, anthropic.APIStatusError):
        return ModelError(f"API error {exc.status_code}: {exc.message}", fatal=False)
    if isinstance(exc, anthropic.APIConnectionError):
        return ModelError("could not reach the API (network or timeout)", fatal=False)
    return exc


class ReplayModel:
    """Hand-written turns returned in order, checking that the loop answered each one.

    Each turn is ``{"stop_reason": ..., "content": [blocks]}`` in the API's JSON shape. Before
    returning a turn, the replay checks that the conversation the loop sent ends as a model
    would expect: the result of every ``tool_use`` of the previous turn, in order, in one user
    message, or the loop's feedback after a final reply. ``requests`` keeps every request.
    """

    def __init__(
        self,
        turns: Sequence[Mapping[str, Any]],
        *,
        model: str = REPLAY_MODEL,
        provenance: str = REPLAY_PROVENANCE,
    ) -> None:
        self._turns = [copy.deepcopy(dict(turn)) for turn in turns]
        if not self._turns:
            raise ReplayError("a replay needs at least one turn")
        for i, turn in enumerate(self._turns):
            if set(turn) != {"stop_reason", "content"} or not isinstance(turn["content"], list):
                raise ReplayError(f"turn {i + 1} must have exactly stop_reason and content")
        self.model = model
        self.provenance = provenance
        self.requests: list[dict[str, Any]] = []

    @property
    def exhausted(self) -> bool:
        return len(self.requests) == len(self._turns)

    def create(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        settings: AgentSettings,
    ) -> ModelTurn:
        index = len(self.requests)
        if index >= len(self._turns):
            raise ReplayError(f"the loop asked for turn {index + 1}; the replay has {index}")
        self.requests.append(
            copy.deepcopy(
                build_request(system=system, messages=messages, tools=tools, settings=settings)
            )
        )
        self._check_conversation(index, messages)
        turn = self._turns[index]
        return ModelTurn(
            content=content_blocks(copy.deepcopy(turn["content"])),
            stop_reason=turn["stop_reason"],
            model=self.model,
        )

    def _check_conversation(self, index: int, messages: list[dict[str, Any]]) -> None:
        if not messages or messages[0]["role"] != "user":
            raise ReplayError("the conversation must open with the user's question")
        if len(messages) == 1:
            return  # a question opens a conversation: the first, or the next of a session
        if index == 0:
            raise ReplayError("the first request must hold the question only")
        previous = self._turns[index - 1]
        if len(messages) < 3 or messages[-2] != {
            "role": "assistant",
            "content": content_blocks(previous["content"]),
        }:
            raise ReplayError(f"turn {index} did not go back into the history unchanged")
        last = messages[-1]
        if last["role"] != "user":
            raise ReplayError(f"request {index + 1} must end with a user message")
        uses = [b["id"] for b in previous["content"] if b.get("type") == "tool_use"]
        if previous["stop_reason"] == "tool_use":
            got = [b.get("tool_use_id") for b in last["content"] if isinstance(b, dict)]
            if got != uses or any(b.get("type") != "tool_result" for b in last["content"]):
                raise ReplayError(f"request {index + 1} must answer tool calls {uses}")
