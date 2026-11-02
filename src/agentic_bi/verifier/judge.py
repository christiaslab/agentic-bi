"""The judge: the only way the verifier reaches a model (ADR-0006).

A ``Judge`` takes a ``JudgeRequest`` (system prompt, user message, and the question and query
they were built from) and returns the model's raw text. The verifier parses that text; a judge
never decides pass or fail itself. Two judges exist: ``ReplayJudge`` (``replay.py``) returns
recorded verdicts for tests, and ``AnthropicJudge`` (``live.py``) calls the model when a key is
present. Neither is ever chosen silently: the caller passes one, or there is no model verdict.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

DEFAULT_MODEL = "claude-haiku-5-5"
MODEL_ENV = "AGENTIC_BI_VERIFIER_MODEL"


def configured_model() -> str:
    """The model id from ``AGENTIC_BI_VERIFIER_MODEL``, else ``DEFAULT_MODEL``."""
    value = os.environ.get(MODEL_ENV, "").strip()
    return value or DEFAULT_MODEL


@dataclass(frozen=True)
class JudgeRequest:
    system: str
    user: str
    question: str
    query: Mapping[str, Any]
    prompt_version: str


@runtime_checkable
class Judge(Protocol):
    """Anything that turns a request into the model's raw text."""

    @property
    def name(self) -> str:
        """How the verdict names its judge, for example a model id or ``replay``."""
        ...

    def judge(self, request: JudgeRequest) -> str: ...


class JudgeError(RuntimeError):
    """The judge returned no usable text (a refusal, a cut-off answer, a missing replay)."""
