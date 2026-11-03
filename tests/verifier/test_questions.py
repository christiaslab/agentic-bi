"""evals/questions.yaml: size, coverage, refusals, paraphrases, golden links, and compiling."""

import copy
from collections import defaultdict

import pytest
import yaml

from agentic_bi.semantic import compile, default_catalog, parse_query
from agentic_bi.verifier import SLICE, run_checks
from agentic_bi.verifier.checks import MAX_LIMIT, MIN_LIMIT
from agentic_bi.verifier.questions import (
    DEFAULT_QUESTIONS_PATH,
    QuestionSetError,
    coverage,
    golden_ids,
    load_question_set,
    normalised_query,
    parse_question_set,
)


@pytest.fixture(scope="module")
def question_set():
    return load_question_set()


@pytest.fixture(scope="module")
def cover(question_set):
    return coverage(question_set)


@pytest.fixture(scope="module")
def raw():
    return yaml.safe_load(DEFAULT_QUESTIONS_PATH.read_text(encoding="utf-8"))


def test_the_set_holds_40_to_60_questions(cover) -> None:
    assert 40 <= cover["questions"] <= 60


def test_every_metric_and_every_grain_is_asked_at_least_once(cover) -> None:
    assert all(n >= 1 for n in cover["metrics"].values()), cover["metrics"]
    assert all(n >= 1 for n in cover["grains"].values()), cover["grains"]


def test_every_dimension_is_grouped_by_at_least_once(cover) -> None:
    assert all(n >= 1 for n in cover["dimensions_grouped"].values()), cover["dimensions_grouped"]


def test_every_operator_is_used_at_least_once(cover) -> None:
    assert all(n >= 1 for n in cover["operators"].values()), cover["operators"]


def test_at_least_ten_top_n_questions_use_order_by_and_limit(question_set) -> None:
    top_n = [q for q in question_set.questions if q.needs_top_n]
    assert len(top_n) >= 10
    for q in top_n:
        assert "order_by" in q.query and "limit" in q.query, q.id


def test_at_least_eight_expected_refusals_of_each_kind(question_set, cover) -> None:
    refusals = [q for q in question_set.questions if q.outcome == "refuse"]
    assert len(refusals) >= 8
    assert all(n >= 1 for n in cover["refusal_reasons"].values()), cover["refusal_reasons"]
    for q in refusals:
        assert q.query is None and q.note, q.id


def test_at_least_five_paraphrase_pairs_share_one_expected_answer(question_set) -> None:
    groups = defaultdict(list)
    for q in question_set.questions:
        if q.paraphrase_group:
            groups[q.paraphrase_group].append(q)
    assert len(groups) >= 5
    for name, members in groups.items():
        assert len(members) >= 2, name
        first = members[0]
        texts = {m.question for m in members}
        assert len(texts) == len(members), name
        for other in members[1:]:
            assert other.outcome == first.outcome, name
            assert normalised_query(other.query) == normalised_query(first.query), name
            assert other.golden_ids == first.golden_ids, name


def test_every_golden_link_names_a_value_of_the_golden_file(question_set) -> None:
    known = golden_ids()
    linked = {g for q in question_set.questions for g in q.golden_ids}
    assert linked and linked <= known


def test_every_answerable_golden_value_family_is_linked(question_set) -> None:
    linked = {g for q in question_set.questions for g in q.golden_ids}
    for prefix in (
        "obligations.total",
        "obligations.fiscal_month.",
        "obligations.recipient.",
        "obligations.psc.",
        "obligations.naics.",
        "obligations.pop_state.",
        "obligations.sub_agency.",
        "obligations.extent_competed.",
        "awards.active_in_period.count",
        "awards.average_net_obligation",
        "recipients.top10_reporting_share",
    ):
        assert any(g.startswith(prefix) for g in linked), prefix


def test_the_twelve_fiscal_months_are_linked_in_order(question_set) -> None:
    months = question_set.by_id("q03").golden_ids
    assert months == tuple(f"obligations.fiscal_month.{m:02d}" for m in range(1, 13))


def test_every_expected_query_without_top_n_passes_the_schema_and_compiles(
    question_set,
) -> None:
    catalog = default_catalog()
    checked = 0
    for q in question_set.questions:
        if q.query is None or q.needs_top_n:
            continue
        compiled = compile(parse_query(q.query), catalog)
        assert compiled.sql.lstrip().upper().startswith(("SELECT", "WITH")), q.id
        checked += 1
    assert checked >= 30


def test_top_n_queries_parse_and_compile_with_their_order_and_limit(question_set) -> None:
    catalog = default_catalog()
    checked = 0
    for q in question_set.questions:
        if not q.needs_top_n:
            continue
        query = parse_query(q.query)
        assert query.order_by is not None and query.limit is not None, q.id
        compiled = compile(query, catalog)
        assert compiled.params[-1] == query.limit, q.id
        checked += 1
    assert checked == 11


def test_top_n_queries_follow_the_order_by_rule(question_set) -> None:
    for q in question_set.questions:
        if not q.needs_top_n:
            continue
        order_by = q.query["order_by"]
        assert set(order_by) == {"field", "direction"}, q.id
        assert order_by["field"] in (q.query["metric"], *q.query.get("dimensions", [])), q.id
        assert order_by["direction"] in ("asc", "desc"), q.id
        assert MIN_LIMIT <= q.query["limit"] <= MAX_LIMIT, q.id


def test_every_expected_query_passes_the_deterministic_checks(question_set) -> None:
    for q in question_set.questions:
        if q.query is not None:
            result = run_checks(q.query)
            assert result.passed, (q.id, result.to_dict())


def test_explicit_time_ranges_stay_inside_the_slice(question_set) -> None:
    for q in question_set.questions:
        if q.query is not None and q.query.get("time_range") is not None:
            parsed = parse_query(q.base_query())
            assert SLICE.start <= parsed.time_range.start <= parsed.time_range.end <= SLICE.end


def test_codes_in_filters_are_text(question_set) -> None:
    for q in question_set.questions:
        for item in (q.query or {}).get("filters", []):
            values = item["value"] if isinstance(item["value"], list) else [item["value"]]
            if item["field"] != "competed":
                assert all(isinstance(v, str) for v in values), q.id


def test_ids_are_sequential(question_set) -> None:
    ids = [q.id for q in question_set.questions]
    assert ids == [f"q{i:02d}" for i in range(1, len(ids) + 1)]


# --- The loader refuses a wrong file ------------------------------------------------------


def _with(raw, index, **change):
    data = copy.deepcopy(raw)
    data["questions"][index].update(change)
    for key, value in change.items():
        if value is None:
            del data["questions"][index][key]
    return data


def _answer_index(raw):
    return next(i for i, q in enumerate(raw["questions"]) if q["outcome"] == "answer")


def _refusal_index(raw):
    return next(i for i, q in enumerate(raw["questions"]) if q["outcome"] == "refuse")


def _top_n_index(raw):
    return next(i for i, q in enumerate(raw["questions"]) if "requires" in q)


@pytest.mark.parametrize(
    ("which", "change", "message"),
    [
        ("answer", {"query": None}, "needs a query"),
        ("answer", {"golden_id": "no.such.value"}, "not in docs/golden"),
        ("answer", {"golden_id": []}, "golden_id"),
        ("answer", {"refusal_reason": "ambiguous"}, "refusal fields"),
        ("answer", {"surprise": 1}, "unknown keys"),
        ("answer", {"outcome": "maybe"}, "outcome"),
        ("answer", {"id": "Q1"}, "two digits"),
        ("answer", {"requires": ["top_n"]}, "top_n is required exactly"),
        ("answer", {"paraphrase_group": "Bad Name"}, "paraphrase_group"),
        ("refusal", {"query": {"metric": "obligations"}}, "no expected query"),
        ("refusal", {"refusal_reason": "because"}, "refusal_reason"),
        ("refusal", {"note": None}, "says why"),
        ("refusal", {"golden_id": "obligations.total"}, "reproduces no golden"),
        ("top_n", {"requires": None}, "top_n is required exactly"),
    ],
)
def test_the_loader_refuses_a_wrong_question(raw, which, change, message) -> None:
    index = {"answer": _answer_index, "refusal": _refusal_index, "top_n": _top_n_index}[which](raw)
    with pytest.raises(QuestionSetError, match=message):
        parse_question_set(_with(raw, index, **change), golden_ids())


def test_the_loader_refuses_a_repeated_id_or_question(raw) -> None:
    data = copy.deepcopy(raw)
    data["questions"].append(copy.deepcopy(data["questions"][0]))
    with pytest.raises(QuestionSetError, match="repeated id"):
        parse_question_set(data, golden_ids())
    data["questions"][-1]["id"] = "q99"
    with pytest.raises(QuestionSetError, match="repeated question"):
        parse_question_set(data, golden_ids())


def test_the_loader_refuses_a_wrong_top_level(raw) -> None:
    with pytest.raises(QuestionSetError):
        parse_question_set({**raw, "extra": 1}, golden_ids())
    with pytest.raises(QuestionSetError):
        parse_question_set({**raw, "version": 2}, golden_ids())
