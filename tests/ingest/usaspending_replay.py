"""Replay of the USAspending responses recorded on 2026-11-01 (tests/fixtures/usaspending/)."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "usaspending"
TRIMMED_CSV = FIXTURES / "contracts_prime_transactions_trimmed.csv"
CAPTURED_AT = datetime(2026, 11, 1, 21, 24, 4, tzinfo=UTC)
SUBAWARD_MEMBER = "Contracts_Subawards_2026-11-01_H21M25S44.csv"


def index() -> dict:
    return json.loads((FIXTURES / "index.json").read_text(encoding="utf-8"))


def trimmed_zip(csv_text: str | None = None, member: str | None = None) -> bytes:
    """The download zip rebuilt from the trimmed CSV, with the recorded member names.

    The recorded subaward member was empty; it is rebuilt empty.
    """
    text = TRIMMED_CSV.read_text(encoding="utf-8") if csv_text is None else csv_text
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member or index()["zip_member"], text)
        archive.writestr(SUBAWARD_MEMBER, "")
    return buffer.getvalue()


class Replay:
    """A transport that answers each recorded (method, URL) with its recorded body."""

    def __init__(self, zip_bytes: bytes | None = None) -> None:
        self.routes: dict[tuple[str, str], list[tuple[int, bytes]]] = {}
        self.calls: list[tuple[str, str, dict | None]] = []
        recorded = index()
        for item in recorded["responses"]:
            body = (FIXTURES / item["file"]).read_bytes()
            self.routes[(item["method"], item["url"])] = [(200, body)]
        self.routes[("GET", recorded["zip_url"])] = [(200, zip_bytes or trimmed_zip())]

    def set(self, method: str, url: str, *answers: tuple[int, bytes]) -> None:
        self.routes[(method, url)] = list(answers)

    def __call__(self, method: str, url: str, body: bytes | None) -> tuple[int, bytes]:
        self.calls.append((method, url, None if body is None else json.loads(body)))
        answers = self.routes.get((method, url))
        if not answers:
            raise AssertionError(f"no recorded response for {method} {url}")
        return answers.pop(0) if len(answers) > 1 else answers[0]


def url_of(file_name: str) -> str:
    """The recorded URL of a fixture response file."""
    for item in index()["responses"]:
        if item["file"] == file_name:
            return item["url"]
    raise KeyError(file_name)
