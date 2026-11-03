"""The eval question set (``evals/questions.yaml``): a strict loader and its coverage.

Field meanings are in ``evals/README.md``. The loader refuses an unknown key, a missing field,
a repeated id or question, an answer with no query, a refusal with a query, a golden id that
``docs/golden/golden.json`` does not hold, and a top-N query not marked ``requires: [top_n]``
(or the reverse). Whether each expected query compiles is the tests' job, not the loader's.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agentic_bi.semantic import Catalog, default_catalog

from .checks import TOP_N_FIELDS

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_QUESTIONS_PATH = ROOT / "evals" / "questions.yaml"
GOLDEN_PATH = ROOT / "docs" / "golden" / "golden.json"

QUESTION_SET_VERSION = 1
OUTCOMES = ("answer", "refuse")
REFUSAL_REASONS = ("out_of_catalog", "outside_slice", "ambiguous")
REQUIREMENTS = ("top_n",)
_ID = re.compile(r"q[0-9]{2}")
_GROUP = re.compile(r"[a-z][a-z0-9_]{0,40}")
QUESTION_KEYS = {
    "id",
    "question",
    "outcome",
    "query",
    "refusal_reason",
    "catalog_candidate",
    "requires",
    "golden_id",
    "paraphrase_group",
    "note",
}


class QuestionSetError(ValueError):
    """The question set file is wrong; the message names the question and the field."""


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    outcome: str
    query: Mapping[str, Any] | None
    refusal_reason: str | None
    catalog_candidate: str | None
    requires: tuple[str, ...]
    golden_ids: tuple[str, ...]
    paraphrase_group: str | None
    note: str | None

    @property
    def needs_top_n(self) -> bool:
        return "top_n" in self.requires

    def base_query(self) -> dict[str, Any] | None:
        """The expected query without ``order_by`` and ``limit``."""
        if self.query is None:
            return None
        return {k: v for k, v in self.query.items() if k not in TOP_N_FIELDS}


@dataclass(frozen=True)
class QuestionSet:
    version: int
    questions: tuple[Question, ...]

    def by_id(self, question_id: str) -> Question:
        for item in self.questions:
            if item.id == question_id:
                return item
        raise KeyError(question_id)


def golden_ids(path: str | Path = GOLDEN_PATH) -> frozenset[str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return frozenset(value["id"] for value in data["values"])


def load_question_set(
    path: str | Path | None = None, golden_path: str | Path = GOLDEN_PATH
) -> QuestionSet:
    source = Path(path) if path is not None else DEFAULT_QUESTIONS_PATH
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    return parse_question_set(data, golden_ids(golden_path))


def parse_question_set(data: Any, known_golden: frozenset[str]) -> QuestionSet:
    if not isinstance(data, Mapping) or set(data) != {"version", "questions"}:
        raise QuestionSetError("the file holds exactly version and questions")
    if data["version"] != QUESTION_SET_VERSION:
        raise QuestionSetError(f"version must be {QUESTION_SET_VERSION}")
    if not isinstance(data["questions"], list):
        raise QuestionSetError("questions must be a list")
    questions = tuple(_question(item, i, known_golden) for i, item in enumerate(data["questions"]))
    for label, values in (
        ("id", [q.id for q in questions]),
        ("question", [q.question for q in questions]),
    ):
        repeated = [value for value, n in Counter(values).items() if n > 1]
        if repeated:
            raise QuestionSetError(f"repeated {label}: {repeated[0]!r}")
    return QuestionSet(version=data["version"], questions=questions)


def _question(item: Any, index: int, known_golden: frozenset[str]) -> Question:
    where = f"questions[{index}]"
    if not isinstance(item, Mapping):
        raise QuestionSetError(f"{where}: expected a mapping")
    unknown = set(item) - QUESTION_KEYS
    if unknown:
        raise QuestionSetError(f"{where}: unknown keys {sorted(unknown)}")
    for key in ("id", "question", "outcome"):
        if not isinstance(item.get(key), str) or not item[key].strip():
            raise QuestionSetError(f"{where}.{key}: required text")
    qid = item["id"]
    where = f"{qid}"
    if not _ID.fullmatch(qid):
        raise QuestionSetError(f"{where}: an id is q followed by two digits")
    outcome = item["outcome"]
    if outcome not in OUTCOMES:
        raise QuestionSetError(f"{where}.outcome: one of {', '.join(OUTCOMES)}")

    query = item.get("query")
    reason = item.get("refusal_reason")
    candidate = item.get("catalog_candidate")
    golden = item.get("golden_id")
    if outcome == "answer":
        if not isinstance(query, Mapping):
            raise QuestionSetError(f"{where}.query: an answer needs a query")
        if reason is not None or candidate is not None:
            raise QuestionSetError(f"{where}: refusal fields on an answer")
    else:
        if query is not None:
            raise QuestionSetError(f"{where}.query: a refusal has no expected query")
        if reason not in REFUSAL_REASONS:
            raise QuestionSetError(f"{where}.refusal_reason: one of {', '.join(REFUSAL_REASONS)}")
        if candidate is not None and not isinstance(candidate, str):
            raise QuestionSetError(f"{where}.catalog_candidate: text")
        if golden is not None:
            raise QuestionSetError(f"{where}.golden_id: a refusal reproduces no golden value")

    requires = item.get("requires", [])
    if not isinstance(requires, list) or any(r not in REQUIREMENTS for r in requires):
        raise QuestionSetError(f"{where}.requires: a list of {', '.join(REQUIREMENTS)}")
    uses_top_n = query is not None and any(key in query for key in TOP_N_FIELDS)
    if uses_top_n != ("top_n" in requires):
        raise QuestionSetError(
            f"{where}.requires: top_n is required exactly when the query has order_by or limit"
        )

    if golden is None:
        golden_list: tuple[str, ...] = ()
    elif isinstance(golden, str):
        golden_list = (golden,)
    elif isinstance(golden, list) and golden and all(isinstance(g, str) for g in golden):
        golden_list = tuple(golden)
    else:
        raise QuestionSetError(f"{where}.golden_id: an id or a non-empty list of ids")
    for gid in golden_list:
        if gid not in known_golden:
            raise QuestionSetError(f"{where}.golden_id: {gid!r} is not in docs/golden/golden.json")
    if len(set(golden_list)) != len(golden_list):
        raise QuestionSetError(f"{where}.golden_id: repeated id")

    group = item.get("paraphrase_group")
    if group is not None and (not isinstance(group, str) or not _GROUP.fullmatch(group)):
        raise QuestionSetError(f"{where}.paraphrase_group: a lowercase name")
    note = item.get("note")
    if note is not None and (not isinstance(note, str) or not note.strip()):
        raise QuestionSetError(f"{where}.note: text")
    if outcome == "refuse" and note is None:
        raise QuestionSetError(f"{where}.note: a refusal says why in a note")

    return Question(
        id=qid,
        question=item["question"],
        outcome=outcome,
        query=query,
        refusal_reason=reason,
        catalog_candidate=candidate,
        requires=tuple(requires),
        golden_ids=golden_list,
        paraphrase_group=group,
        note=note,
    )


def normalised_query(query: Mapping[str, Any]) -> dict[str, Any]:
    """The query with defaults filled in, for comparing two expected queries."""
    return {"dimensions": [], "filters": [], "grain": "total", "time_range": None, **query}


def coverage(question_set: QuestionSet, catalog: Catalog | None = None) -> dict[str, Any]:
    """How often the set uses each metric, grain and dimension, and its other counts."""
    catalog = catalog if catalog is not None else default_catalog()
    metrics = Counter({name: 0 for name in catalog.metrics})
    grains = Counter({name: 0 for name in catalog.grains})
    grouped = Counter({name: 0 for name in catalog.dimensions})
    filtered = Counter({name: 0 for name in catalog.dimensions})
    operators = Counter({name: 0 for name in catalog.operators})
    reasons = Counter({name: 0 for name in REFUSAL_REASONS})
    groups: dict[str, list[str]] = {}
    for q in question_set.questions:
        if q.paraphrase_group:
            groups.setdefault(q.paraphrase_group, []).append(q.id)
        if q.query is None:
            reasons[q.refusal_reason] += 1
            continue
        full = normalised_query(q.query)
        metrics[full["metric"]] += 1
        grains[full["grain"]] += 1
        for name in full["dimensions"]:
            grouped[name] += 1
        for item in full["filters"]:
            filtered[item["field"]] += 1
            operators[item["op"]] += 1
    return {
        "questions": len(question_set.questions),
        "answers": sum(q.outcome == "answer" for q in question_set.questions),
        "refusals": sum(q.outcome == "refuse" for q in question_set.questions),
        "refusal_reasons": dict(reasons),
        "top_n": sum(q.needs_top_n for q in question_set.questions),
        "golden_linked": sum(bool(q.golden_ids) for q in question_set.questions),
        "golden_ids": len({g for q in question_set.questions for g in q.golden_ids}),
        "paraphrase_groups": groups,
        "metrics": dict(metrics),
        "grains": dict(grains),
        "dimensions_grouped": dict(grouped),
        "dimensions_filtered": dict(filtered),
        "operators": dict(operators),
    }
