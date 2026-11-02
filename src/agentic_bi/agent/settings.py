"""Settings of the analyst agent: the model and the limits of one question.

The model id is a setting (ADR-0005), never a constant in a prompt: ``AgentSettings.model``,
read from ``AGENTIC_BI_MODEL`` or ``--model`` and defaulting to ``DEFAULT_MODEL``. The other
fields bound what one question may cost: model calls, tool calls, answer attempts, the rows
one query may return to the model, and the query timeout.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from typing import Any

DEFAULT_MODEL = "claude-sonnet-5-5"
MODEL_ENV = "AGENTIC_BI_MODEL"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
THINKING_DISPLAYS = ("summarized", "omitted")


@dataclass(frozen=True)
class AgentSettings:
    """Everything the loop needs besides the question, the model client and the tools."""

    model: str = DEFAULT_MODEL
    # One response of the model, thinking included; 16,000 keeps a non-streamed request well
    # inside the SDK's timeouts.
    max_tokens: int = 16_000
    effort: str = "high"
    thinking_display: str = "summarized"
    # Per question.
    max_model_calls: int = 12
    max_tool_calls: int = 16
    max_answer_attempts: int = 2
    # Per run_query call: more rows is an error that asks for a narrower query or a top-N.
    max_rows: int = 200
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model must be a non-empty model id")
        if self.effort not in EFFORTS:
            raise ValueError(f"effort must be one of {', '.join(EFFORTS)}")
        if self.thinking_display not in THINKING_DISPLAYS:
            raise ValueError(f"thinking_display must be one of {', '.join(THINKING_DISPLAYS)}")
        for name in ("max_tokens", "max_model_calls", "max_tool_calls", "max_answer_attempts"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if type(self.max_rows) is not int or not 1 <= self.max_rows <= 10_000:
            raise ValueError("max_rows must be an integer from 1 to 10000")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None, **overrides: Any) -> AgentSettings:
        """Defaults, then ``AGENTIC_BI_MODEL``, then explicit overrides that are not None."""
        environ = os.environ if environ is None else environ
        settings = cls()
        model = environ.get(MODEL_ENV, "").strip()
        if model:
            settings = replace(settings, model=model)
        given = {key: value for key, value in overrides.items() if value is not None}
        unknown = sorted(set(given) - {f.name for f in fields(cls)})
        if unknown:
            raise TypeError(f"unknown settings: {', '.join(unknown)}")
        return replace(settings, **given)

    def to_dict(self) -> dict[str, Any]:
        """The settings as recorded in a transcript."""
        return {f.name: getattr(self, f.name) for f in fields(self)}
