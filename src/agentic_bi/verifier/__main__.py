"""Verify one question and one structured query from the command line.

    uv run python -m agentic_bi.verifier check --question <text> --query <json>

Prints one JSON object: the deterministic checks and the verdict. With no
``ANTHROPIC_API_KEY`` only the deterministic checks run; a query that passes them has no
verdict, and the output says intent was not judged. With a key, the live judge gives the
model verdict. ``--replay <dir>`` uses recorded verdicts instead, with no key and no network.

Exit status: 0 for a consistent verdict; 1 for an inconsistent or refuse_expected verdict; 2
when the run itself failed (bad arguments, no live judge available, a failed model call, a
missing replay, a malformed verdict); 3 when the checks passed and intent was not judged, so
a script never reads an unjudged query as verified.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, TextIO

from .checks import CheckResult
from .judge import Judge, JudgeError, configured_model
from .live import AnthropicJudge, LiveJudgeUnavailable
from .replay import ReplayFileError, ReplayJudge
from .verdict import Issue, MalformedVerdictError
from .verify import Verification, VerifierInputError, check_question, verify

KEY_ENV = "ANTHROPIC_API_KEY"
_NOT_JSON = object()
NOT_JUDGED = 3


def main(argv: list[str] | None = None, out: TextIO | None = None) -> int:
    out = out if out is not None else sys.stdout
    parser = argparse.ArgumentParser(
        prog="python -m agentic_bi.verifier", description=__doc__.splitlines()[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="verify one question and one structured query")
    check.add_argument("--question", required=True, help="the natural-language question")
    check.add_argument("--query", required=True, help="the structured query, as JSON text")
    check.add_argument("--replay", help="a directory of replay files to judge from")
    check.add_argument("--model", help=f"the live judge's model id (default: {configured_model()})")
    args = parser.parse_args(argv)

    try:
        query: Any = json.loads(args.query)
    except json.JSONDecodeError:
        query = _NOT_JSON
    report: dict[str, Any] = {}
    try:
        judge, note, unavailable = _choose_judge(args.replay, args.model)
        if query is _NOT_JSON:
            # Not JSON at all: the schema check fails before anything else.
            check_question(args.question)
            checks = CheckResult(
                (
                    Issue(
                        code="invalid_query",
                        field="query",
                        explanation="the query is not valid JSON",
                        refusal_code="invalid_query",
                    ),
                ),
                None,
            )
            verification = Verification(args.question, args.query, checks, checks.verdict())
            report = verification.to_dict()
        else:
            verification = verify(args.question, query, judge=judge)
            report = verification.to_dict()
        if verification.verdict is None:
            report["note"] = note or "intent not judged"
            if unavailable:
                report["error"] = note
        elif verification.verdict.stage == "deterministic":
            report["note"] = "decided by the deterministic checks; no model was called"
        elif isinstance(judge, ReplayJudge) and judge.served:
            replay_id = judge.served[-1]
            report["note"] = (
                f"verdict from replay {replay_id!r}, provenance "
                f"{judge.provenance_of(replay_id)}; not a live model call"
            )
    except (
        JudgeError,
        MalformedVerdictError,
        ReplayFileError,
        VerifierInputError,
    ) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        json.dump(report, out, indent=2, ensure_ascii=False, default=str)
        out.write("\n")
        return 2

    json.dump(report, out, indent=2, ensure_ascii=False, default=str)
    out.write("\n")
    if "error" in report:
        return 2
    verdict = verification.verdict
    if verdict is None:
        return NOT_JUDGED
    if verdict.verdict == "consistent":
        return 0
    return 1


def _choose_judge(replay: str | None, model: str | None) -> tuple[Judge | None, str | None, bool]:
    """The judge to use, a note when there is none, and whether a wanted judge is missing."""
    if replay is not None:
        return ReplayJudge.from_directory(replay), None, False
    if not os.environ.get(KEY_ENV, "").strip():
        return (
            None,
            f"model verdict not run: {KEY_ENV} is not set; deterministic checks only",
            False,
        )
    try:
        return AnthropicJudge(model=model or configured_model()), None, False
    except LiveJudgeUnavailable as exc:
        return None, f"model verdict not run: {exc}", True


if __name__ == "__main__":
    sys.exit(main())
