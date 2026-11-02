"""The verifier of ADR-0002 point 8 and ADR-0006: does a structured query answer its question?

``verify(question, query, judge=...)`` runs the deterministic checks (schema, catalog, slice)
and, when they pass and a judge is given, asks the judge whether the query matches the
question's intent as the catalog defines it. The judge returns text; ``parse_verdict`` reads
it against ``VERDICT_JSON_SCHEMA`` and a malformed verdict is an error, never a pass. The
verifier judges intent, never SQL. Reading guide: docs/verifier.md.
"""

from .checks import SLICE, CheckResult, run_checks
from .excerpt import catalog_excerpt
from .judge import DEFAULT_MODEL, Judge, JudgeError, JudgeRequest
from .live import AnthropicJudge, LiveJudgeUnavailable
from .prompt import PROMPT_VERSION, load_prompt
from .replay import ReplayJudge
from .verdict import (
    DETERMINISTIC_ISSUE_CODES,
    ISSUE_CODES,
    MODEL_ISSUE_CODES,
    VERDICT_JSON_SCHEMA,
    VERDICTS,
    Issue,
    MalformedVerdictError,
    Verdict,
    parse_verdict,
)
from .verify import Verification, VerifierInputError, verify

__all__ = [
    "DEFAULT_MODEL",
    "DETERMINISTIC_ISSUE_CODES",
    "ISSUE_CODES",
    "MODEL_ISSUE_CODES",
    "PROMPT_VERSION",
    "SLICE",
    "VERDICTS",
    "VERDICT_JSON_SCHEMA",
    "AnthropicJudge",
    "CheckResult",
    "Issue",
    "Judge",
    "JudgeError",
    "JudgeRequest",
    "LiveJudgeUnavailable",
    "MalformedVerdictError",
    "ReplayJudge",
    "Verdict",
    "Verification",
    "VerifierInputError",
    "catalog_excerpt",
    "load_prompt",
    "parse_verdict",
    "run_checks",
    "verify",
]
