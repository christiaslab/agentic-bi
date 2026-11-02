"""A judge that returns recorded verdicts, so the verifier is proven with no key and no network.

A replay file is a JSON object:

- ``replay_id``: a short name, unique in its directory;
- ``provenance``: ``hand_written`` (composed by hand from the verdict schema, never model
  output) or ``recorded`` (captured from a live run, which S5b may add);
- ``note``: one line on what the replay shows;
- ``prompt_version``: the prompt it answers; a replay for another version is never served;
- ``question`` and ``query``: what the judge is asked;
- ``response``: the judge's text, given as a JSON object (written out as JSON text) or as a
  string (served as is, which lets a test serve a malformed answer).

Optional: ``question_id``, the id of the question in ``evals/questions.yaml``.

A request with no matching replay is a ``JudgeError``; the replay judge never falls back to a
default verdict.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .judge import JudgeError, JudgeRequest

PROVENANCES = ("hand_written", "recorded")
_REQUIRED = ("replay_id", "provenance", "note", "prompt_version", "question", "query", "response")
_OPTIONAL = ("question_id",)


class ReplayFileError(ValueError):
    """A replay file does not have the shape described in this module."""


@dataclass(frozen=True)
class Replay:
    replay_id: str
    provenance: str
    note: str
    prompt_version: str
    question: str
    query: Mapping[str, Any]
    response: str
    question_id: str | None = None


def canonical_json(value: Any) -> str:
    """One text per JSON value: sorted keys, no spaces, unicode kept."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def parse_replay(data: Any, where: str = "replay") -> Replay:
    if not isinstance(data, Mapping):
        raise ReplayFileError(f"{where}: expected a JSON object")
    for key in data:
        if key not in _REQUIRED + _OPTIONAL:
            raise ReplayFileError(f"{where}: {key!r} is not a field of a replay")
    for key in _REQUIRED:
        if key not in data:
            raise ReplayFileError(f"{where}: {key!r} is required")
    for key in ("replay_id", "note", "prompt_version", "question"):
        if not isinstance(data[key], str) or not data[key].strip():
            raise ReplayFileError(f"{where}: {key!r} must be non-empty text")
    if data["provenance"] not in PROVENANCES:
        raise ReplayFileError(f"{where}: provenance must be one of {', '.join(PROVENANCES)}")
    if not isinstance(data["query"], Mapping):
        raise ReplayFileError(f"{where}: query must be a JSON object")
    response = data["response"]
    if isinstance(response, Mapping):
        response = json.dumps(response, ensure_ascii=False)
    elif not isinstance(response, str):
        raise ReplayFileError(f"{where}: response must be a JSON object or text")
    question_id = data.get("question_id")
    if question_id is not None and not isinstance(question_id, str):
        raise ReplayFileError(f"{where}: question_id must be text")
    return Replay(
        replay_id=data["replay_id"],
        provenance=data["provenance"],
        note=data["note"],
        prompt_version=data["prompt_version"],
        question=data["question"],
        query=data["query"],
        response=response,
        question_id=question_id,
    )


def load_replays(directory: str | Path) -> list[Replay]:
    """Every ``*.json`` replay in ``directory``, in file-name order."""
    root = Path(directory)
    if not root.is_dir():
        raise ReplayFileError(f"{root}: not a directory of replays")
    replays = []
    for path in sorted(root.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ReplayFileError(f"{path.name}: not valid JSON: {exc}") from exc
        replays.append(parse_replay(data, path.name))
    return replays


class ReplayJudge:
    """Serves the recorded response whose question, query and prompt version all match."""

    def __init__(self, replays: Iterable[Replay]) -> None:
        self._by_key: dict[tuple[str, str, str], Replay] = {}
        ids: set[str] = set()
        for replay in replays:
            if replay.replay_id in ids:
                raise ReplayFileError(f"replay_id {replay.replay_id!r} is used twice")
            ids.add(replay.replay_id)
            key = _key(replay.question, replay.query, replay.prompt_version)
            if key in self._by_key:
                raise ReplayFileError(
                    f"{replay.replay_id!r} and {self._by_key[key].replay_id!r} answer the same "
                    "question and query"
                )
            self._by_key[key] = replay
        self.served: list[str] = []

    @classmethod
    def from_directory(cls, directory: str | Path) -> ReplayJudge:
        return cls(load_replays(directory))

    @property
    def name(self) -> str:
        return "replay"

    def judge(self, request: JudgeRequest) -> str:
        replay = self._by_key.get(_key(request.question, request.query, request.prompt_version))
        if replay is None:
            raise JudgeError(
                "no replay answers this question and query for prompt version "
                f"{request.prompt_version}"
            )
        self.served.append(replay.replay_id)
        return replay.response

    def provenance_of(self, replay_id: str) -> str:
        for replay in self._by_key.values():
            if replay.replay_id == replay_id:
                return replay.provenance
        raise KeyError(replay_id)


def _key(question: str, query: Any, prompt_version: str) -> tuple[str, str, str]:
    return (question, canonical_json(query), prompt_version)
