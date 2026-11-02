"""The verifier: deterministic checks first, then the judge, for one question and one query."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agentic_bi.semantic import Catalog, default_catalog

from .checks import CheckResult, run_checks
from .excerpt import catalog_excerpt
from .judge import Judge, JudgeRequest
from .prompt import PROMPT_VERSION, load_prompt, render_user_message
from .verdict import Verdict, parse_verdict

MAX_QUESTION_LENGTH = 2000


class VerifierInputError(ValueError):
    """The question itself is unusable (empty or too long); nothing was judged."""


@dataclass(frozen=True)
class Verification:
    """The checks, and the verdict: deterministic, from the judge, or None when not judged."""

    question: str
    query: Any
    checks: CheckResult
    verdict: Verdict | None

    @property
    def judged(self) -> bool:
        return self.verdict is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "query": self.query,
            "deterministic": self.checks.to_dict(),
            "verdict": None if self.verdict is None else self.verdict.to_dict(),
        }


def check_question(question: Any) -> None:
    """Raise ``VerifierInputError`` unless the question is non-empty text of a usable length."""
    if not isinstance(question, str) or not question.strip():
        raise VerifierInputError("the question must be non-empty text")
    if len(question) > MAX_QUESTION_LENGTH:
        raise VerifierInputError(f"the question is longer than {MAX_QUESTION_LENGTH} characters")


def build_request(
    question: str, query: Mapping[str, Any], catalog: Catalog | None = None
) -> JudgeRequest:
    """The judge request for a question and a query that passed the checks."""
    return JudgeRequest(
        system=load_prompt(PROMPT_VERSION),
        user=render_user_message(question, query, catalog_excerpt(catalog)),
        question=question,
        query=query,
        prompt_version=PROMPT_VERSION,
    )


def verify(
    question: str,
    query: Any,
    *,
    judge: Judge | None = None,
    catalog: Catalog | None = None,
) -> Verification:
    """Check ``query`` against the catalog and the slice, then ask ``judge`` about intent.

    A failed check returns its deterministic verdict and the judge is not called. With no
    judge, a query that passes the checks has no verdict: intent was not judged. A judge
    whose text is not a valid verdict raises ``MalformedVerdictError``; it is never a pass.
    """
    check_question(question)
    catalog = catalog if catalog is not None else default_catalog()
    checks = run_checks(query, catalog)
    if not checks.passed:
        return Verification(question, query, checks, checks.verdict())
    if judge is None:
        return Verification(question, query, checks, None)
    request = build_request(question, query, catalog)
    text = judge.judge(request)
    verdict = parse_verdict(text, judge=judge.name, prompt_version=request.prompt_version)
    return Verification(question, query, checks, verdict)
