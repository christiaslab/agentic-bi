"""The analyst's system prompt: a versioned file, filled from the slice and the refusal codes.

The prompt holds no model id (the model is a setting, ADR-0005) and no catalog content: the
model reads the catalog through its tool. ``PROMPT_VERSION`` names the file; a transcript
records the version and the sha256 of the text the model was given.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from agentic_bi.ingest import spec

from .answer import COMPILER_REFUSAL_CODES

PROMPT_VERSION = "analyst-v1"
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / f"{PROMPT_VERSION}.md"


@lru_cache(maxsize=1)
def system_prompt() -> str:
    """The system prompt text, with every ``{{placeholder}}`` filled."""
    text = PROMPT_PATH.read_text(encoding="utf-8")
    values = {
        "agency": spec.AGENCY_NAME,
        "award_types": ", ".join(spec.AWARD_TYPE_CODES),
        "start": spec.START_DATE,
        "end": spec.END_DATE,
        "fiscal_year": str(spec.FISCAL_YEAR),
        "compiler_codes": ", ".join(f"`{code}`" for code in COMPILER_REFUSAL_CODES),
    }
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    if "{{" in text or "}}" in text:
        raise ValueError(f"{PROMPT_PATH.name} has a placeholder with no value")
    return text


def prompt_sha256() -> str:
    return hashlib.sha256(system_prompt().encode("utf-8")).hexdigest()
