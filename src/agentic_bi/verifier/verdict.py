"""The verifier's verdict: its values, its typed issue codes, its JSON schema and a strict parser.

A verdict says whether a structured query answers a question as the catalog defines it
(ADR-0006). It is one of three values:

- ``consistent``: the query answers the question as asked; no issue.
- ``inconsistent``: the query answers a different question; at least one issue says how.
- ``refuse_expected``: the question cannot be answered in the catalog or the slice, so any
  query is a substitute and the right answer is a refusal.

Issue codes come in two families. Deterministic codes are raised by ``checks`` before any model
call and never by a model. Model codes are the ones the judge may use; ``VERDICT_JSON_SCHEMA``
lists only those. ``parse_verdict`` reads the model's text against that schema and the rules
between verdict and issues; anything else is a ``MalformedVerdictError``, never a pass.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

VERDICTS = ("consistent", "inconsistent", "refuse_expected")

# Raised by the deterministic checks only; a model verdict that uses one is malformed.
DETERMINISTIC_ISSUE_CODES: Mapping[str, str] = MappingProxyType(
    {
        "invalid_query": "The query does not match the structured-query JSON schema.",
        "outside_catalog": (
            "The query names a metric, dimension, grain, operator or filter value outside the "
            "catalog, or a dimension the metric does not allow."
        ),
        "time_range_outside_slice": (
            "The time range reaches dates the warehouse does not hold (the slice is EPA "
            "contracts in fiscal year 2025, 2024-10-01 to 2025-09-30)."
        ),
    }
)

# The judge's vocabulary. The prompt defines each one again in the model's terms.
MODEL_ISSUE_CODES: Mapping[str, str] = MappingProxyType(
    {
        "wrong_metric": "The query measures something other than what the question asks for.",
        "wrong_dimension": (
            "The query groups by a dimension the question does not ask for, or misses one "
            "it asks for."
        ),
        "missing_filter": "The question restricts the selection and the query does not.",
        "wrong_filter": (
            "The query filters on something the question does not ask for, or uses the wrong "
            "field, operator or value."
        ),
        "wrong_grain": "The query's time breakdown differs from the one the question asks for.",
        "wrong_time_range": "The query's dates differ from the period the question names.",
        "wrong_order": (
            "A top-N or bottom-N question has the wrong order_by or limit, or a question "
            "with no ranking gets one."
        ),
        "should_refuse": (
            "The question cannot be answered as the catalog defines its terms; any query is "
            "a substitute and the right answer is a refusal."
        ),
    }
)

ISSUE_CODES: Mapping[str, str] = MappingProxyType(
    {**DETERMINISTIC_ISSUE_CODES, **MODEL_ISSUE_CODES}
)

MAX_ISSUES = 10
MAX_FIELD_LENGTH = 200
MAX_EXPLANATION_LENGTH = 1000
MAX_RATIONALE_LENGTH = 2000

VERDICT_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://github.com/christiaslab/agentic-bi/schemas/verifier-verdict-v1.json",
    "title": "Verifier verdict",
    "description": (
        "Whether a structured query answers a question as the catalog defines it, with typed "
        "issues. Intent only, never SQL."
    ),
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "issues", "rationale"],
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "issues": {
            "type": "array",
            "maxItems": MAX_ISSUES,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["code", "field", "explanation"],
                "properties": {
                    "code": {"type": "string", "enum": list(MODEL_ISSUE_CODES)},
                    "field": {
                        "description": "The part of the query concerned, such as filters[0].",
                        "type": ["string", "null"],
                        "maxLength": MAX_FIELD_LENGTH,
                    },
                    "explanation": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_EXPLANATION_LENGTH,
                    },
                },
            },
        },
        "rationale": {"type": "string", "minLength": 1, "maxLength": MAX_RATIONALE_LENGTH},
    },
}


class MalformedVerdictError(ValueError):
    """The judge's text is not a verdict of the schema; the query is neither passed nor failed."""

    def __init__(self, where: str, message: str) -> None:
        self.where = where
        self.message = message
        super().__init__(f"{where}: {message}")


@dataclass(frozen=True)
class Issue:
    """One typed reason a query does not answer its question."""

    code: str
    field: str | None
    explanation: str
    # The compiler's refusal code, for deterministic issues that come from a refusal.
    refusal_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code,
            "field": self.field,
            "explanation": self.explanation,
        }
        if self.refusal_code is not None:
            out["refusal_code"] = self.refusal_code
        return out


@dataclass(frozen=True)
class Verdict:
    """A verdict, from the deterministic checks or from a judge."""

    verdict: str
    issues: tuple[Issue, ...]
    rationale: str
    stage: str  # "deterministic" or "model"
    judge: str | None = None
    prompt_version: str | None = None

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "issues": [issue.to_dict() for issue in self.issues],
            "rationale": self.rationale,
            "stage": self.stage,
            "judge": self.judge,
            "prompt_version": self.prompt_version,
        }


def verdict_json_schema() -> dict[str, Any]:
    """A copy of the verdict JSON schema, safe for the caller to change."""
    return copy.deepcopy(VERDICT_JSON_SCHEMA)


# One fenced block around the whole answer is tolerated; nothing else around the JSON is.
_FENCE = re.compile(r"```(?:json)?[ \t]*\n(?P<body>.*)\n[ \t]*```", re.DOTALL)


def parse_verdict(
    text: Any, *, judge: str | None = None, prompt_version: str | None = None
) -> Verdict:
    """Read a judge's raw text as a model verdict, or raise ``MalformedVerdictError``."""
    if not isinstance(text, str):
        raise MalformedVerdictError("response", "expected text")
    body = text.strip()
    fenced = _FENCE.fullmatch(body)
    if fenced is not None:
        body = fenced.group("body").strip()
    try:
        data = json.loads(body, object_pairs_hook=_no_duplicate_keys, parse_constant=_no_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise MalformedVerdictError("response", f"is not one JSON object: {exc}") from exc
    validate(data, VERDICT_JSON_SCHEMA)
    issues = tuple(
        Issue(code=item["code"], field=item["field"], explanation=item["explanation"])
        for item in data["issues"]
    )
    check_verdict_rules(data["verdict"], tuple(issue.code for issue in issues))
    return Verdict(
        verdict=data["verdict"],
        issues=issues,
        rationale=data["rationale"],
        stage="model",
        judge=judge,
        prompt_version=prompt_version,
    )


def check_verdict_rules(verdict: str, codes: tuple[str, ...]) -> None:
    """The rules between a verdict and its issue codes, for model and deterministic verdicts."""
    refusal_codes = {"should_refuse", "time_range_outside_slice"}
    if verdict == "consistent" and codes:
        raise MalformedVerdictError("issues", "a consistent verdict carries no issue")
    if verdict == "inconsistent":
        if not codes:
            raise MalformedVerdictError("issues", "an inconsistent verdict names an issue")
        if "should_refuse" in codes:
            raise MalformedVerdictError(
                "issues", "should_refuse goes with the verdict refuse_expected"
            )
    if verdict == "refuse_expected" and not refusal_codes & set(codes):
        raise MalformedVerdictError(
            "issues", "a refuse_expected verdict names should_refuse or time_range_outside_slice"
        )


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"repeated key {key!r}")
        out[key] = value
    return out


def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


# --- A validator for the subset of JSON Schema the verdict schema uses --------------------

_ANNOTATIONS = frozenset({"$schema", "$id", "title", "description"})
_KEYWORDS = frozenset(
    {
        "type",
        "enum",
        "required",
        "additionalProperties",
        "properties",
        "items",
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
    }
)


def validate(value: Any, schema: Mapping[str, Any], where: str = "verdict") -> None:
    """Check ``value`` against ``schema``; raise ``MalformedVerdictError`` naming the path.

    Only the keywords the verdict schema uses are known. A schema with any other keyword is a
    programming error (``ValueError``), so the schema can never ask for a check that is
    silently skipped.
    """
    unknown = set(schema) - _KEYWORDS - _ANNOTATIONS
    if unknown:
        raise ValueError(f"unsupported schema keywords at {where}: {sorted(unknown)}")
    if "type" in schema and not _has_type(value, schema["type"]):
        raise MalformedVerdictError(where, f"expected {schema['type']}")
    if "enum" in schema and value not in schema["enum"]:
        raise MalformedVerdictError(where, f"expected one of {', '.join(map(str, schema['enum']))}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise MalformedVerdictError(where, "is empty")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise MalformedVerdictError(where, f"is longer than {schema['maxLength']} characters")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise MalformedVerdictError(where, f"expected at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise MalformedVerdictError(where, f"expected at most {schema['maxItems']} items")
        if "items" in schema:
            for i, item in enumerate(value):
                validate(item, schema["items"], f"{where}[{i}]")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if schema.get("additionalProperties", True) is False:
            for key in value:
                if key not in properties:
                    raise MalformedVerdictError(f"{where}.{key}", "is not a field of the verdict")
        for key in schema.get("required", ()):
            if key not in value:
                raise MalformedVerdictError(f"{where}.{key}", "is required")
        for key, subschema in properties.items():
            if key in value:
                validate(value[key], subschema, f"{where}.{key}")


def _has_type(value: Any, expected: str | list[str]) -> bool:
    names = [expected] if isinstance(expected, str) else expected
    checks = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: type(v) is int,
        "boolean": lambda v: type(v) is bool,
        "null": lambda v: v is None,
    }
    for name in names:
        if name not in checks:
            raise ValueError(f"unsupported schema type {name!r}")
    return any(checks[name](value) for name in names)
