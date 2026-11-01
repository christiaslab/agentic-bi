"""The attribution rules of scripts/commit_hygiene.py on single messages."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "commit_hygiene.py"
spec = importlib.util.spec_from_file_location("commit_hygiene", SCRIPT)
commit_hygiene = importlib.util.module_from_spec(spec)
spec.loader.exec_module(commit_hygiene)

# Built by concatenation so this file itself carries no trailer line.
CO_AUTHOR = "Co-" + "Authored-By: Someone <someone@example.invalid>"
GENERATED = "Generated" + " with a tool"


def test_clean_message_passes() -> None:
    assert commit_hygiene.inspect_message("feat(ingest): add the fetch script\n\nBody.") == []


def test_co_author_trailer_fails() -> None:
    message = "fix: a thing\n\n" + CO_AUTHOR
    assert commit_hygiene.inspect_message(message) == ["attribution-trailer"]


def test_generated_signature_fails() -> None:
    message = "docs: notes\n\n" + GENERATED
    assert commit_hygiene.inspect_message(message) == ["generated-signature"]
