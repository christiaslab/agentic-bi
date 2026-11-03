"""The verifier's prompt: a versioned file in this package, and the message built around it.

``prompts/verifier-<version>.md`` is the system prompt; its first line names its version and
must equal ``PROMPT_VERSION``. A change to the prompt's meaning is a new file and a new
version, so a recorded verdict always names the prompt it answered (replays check this).

The user message carries the catalog excerpt, the question and the structured query as three
JSON values between tags. Every ``<`` and ``>`` inside them is written as a JSON unicode escape,
so a question cannot close its own tag and pose as an instruction; the prompt says the blocks
are data.
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

PROMPT_VERSION = "v1"
_VERSION_LINE = "prompt_version: "


def prompt_path(version: str = PROMPT_VERSION) -> str:
    """The package-relative name of a prompt file."""
    return f"prompts/verifier-{version}.md"


def load_prompt(version: str = PROMPT_VERSION) -> str:
    """The system prompt of ``version``, checked against the version line it opens with."""
    text = resources.files(__package__).joinpath(prompt_path(version)).read_text(encoding="utf-8")
    first = text.split("\n", 1)[0]
    if first != f"{_VERSION_LINE}{version}":
        raise ValueError(f"{prompt_path(version)} opens with {first!r}, not version {version}")
    return text


def data_json(value: Any) -> str:
    """JSON text of ``value`` that holds no ``<``, ``>`` or ``&`` character."""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render_user_message(question: str, query: Any, excerpt: Any) -> str:
    """The user message: catalog excerpt, question and structured query, each as JSON."""
    return (
        f"<catalog>\n{data_json(excerpt)}\n</catalog>\n\n"
        f"<question>\n{data_json(question)}\n</question>\n\n"
        f"<structured_query>\n{data_json(query)}\n</structured_query>\n\n"
        "Judge whether the structured query answers the question as the catalog defines it. "
        "Answer with the JSON verdict only."
    )
