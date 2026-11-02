"""The live judge: one Messages API call per verdict, through an injected or lazily built client.

This module does not add a dependency. The ``anthropic`` package arrives with S4 (ADR-0005)
and is imported only when a live judge is built without a client. Tests pass a fake client,
so nothing here needs a key or the network. S5b wires this judge into the eval run, with the
pinned model and the spend cap of the plan.

The key is read by the client library from the environment; this module never reads, prints
or stores it.
"""

from __future__ import annotations

from typing import Any

from .judge import JudgeError, JudgeRequest, configured_model

# Thinking counts toward this budget on models that think by default, so it is generous.
MAX_TOKENS = 16000
EFFORT = "low"


class LiveJudgeUnavailable(RuntimeError):
    """A live judge cannot be built here (the client library is not installed)."""


class AnthropicJudge:
    """Asks a Claude model for the verdict text with the versioned system prompt."""

    def __init__(self, client: Any = None, model: str | None = None) -> None:
        self.model = model or configured_model()
        self._client = client if client is not None else _default_client()

    @property
    def name(self) -> str:
        return self.model

    def judge(self, request: JudgeRequest) -> str:
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=request.system,
                messages=[{"role": "user", "content": request.user}],
                output_config={"effort": EFFORT},
            )
        except Exception as exc:
            # A network, auth, rate or API error is a failed run, never a verdict. Only the
            # error's type is kept: its text could echo request details.
            raise JudgeError(f"the model call failed: {type(exc).__name__}") from exc
        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            raise JudgeError("the model declined to answer")
        if stop == "max_tokens":
            raise JudgeError(f"the answer was cut off at {MAX_TOKENS} tokens")
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        if not text.strip():
            raise JudgeError("the model returned no text")
        return text


def _default_client() -> Any:
    try:
        import anthropic  # noqa: PLC0415 - optional until S4 adds the client library
    except ImportError as exc:
        raise LiveJudgeUnavailable(
            "the anthropic client library is not installed in this environment; it arrives "
            "with S4, and S5b wires the live judge"
        ) from exc
    return anthropic.Anthropic()
