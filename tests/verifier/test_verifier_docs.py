"""The verifier's documents must keep up with the code and the question set."""

from pathlib import Path

import pytest

from agentic_bi.verifier import ISSUE_CODES, VERDICTS
from agentic_bi.verifier.questions import QUESTION_KEYS, REFUSAL_REASONS

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs" / "verifier.md"
ADR = ROOT / "docs" / "decisions" / "0006-verifier-and-eval-set.md"
EVALS = ROOT / "evals" / "README.md"


@pytest.mark.parametrize("path", [GUIDE, ADR])
def test_every_verdict_and_issue_code_is_named(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for name in (*VERDICTS, *ISSUE_CODES):
        assert f"`{name}`" in text, name


def test_the_evals_readme_explains_every_field_and_refusal_reason() -> None:
    text = EVALS.read_text(encoding="utf-8")
    for name in (*QUESTION_KEYS, *REFUSAL_REASONS):
        assert f"`{name}`" in text, name
    assert "docs/golden/golden.json" in text


@pytest.mark.parametrize(
    "path",
    [
        GUIDE,
        ADR,
        EVALS,
        ROOT / "evals" / "questions.yaml",
        ROOT / "tests/verifier/replays/README.md",
    ],
)
def test_no_em_or_en_dash(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert chr(0x2014) not in text and chr(0x2013) not in text
