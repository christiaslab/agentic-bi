"""Tests for the golden and mapping loaders of agentic_bi.reproduce, on the real files and on
small documents built in the test."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from agentic_bi.reproduce.mapping import (
    DEFAULT_GOLDEN,
    DEFAULT_MAPPING,
    Golden,
    GoldenValue,
    MappedQuery,
    MappingError,
    NotExpressible,
    load_golden,
    load_mapping,
    parse_mapping,
)
from agentic_bi.semantic import StructuredQuery

CAPTURE_DATE = "2026-11-01"
QUERY = {"metric": "obligations", "time_range": {"start": "2024-10-01", "end": "2025-09-30"}}


def _golden(*ids: str) -> Golden:
    return Golden(
        capture_date=CAPTURE_DATE,
        values=tuple(GoldenValue(i, "owner_api", "1.00", "usd", "") for i in ids),
    )


def _query_entry(value_id: str = "a", **extra: Any) -> dict[str, Any]:
    return {"id": value_id, "query": copy.deepcopy(QUERY), **extra}


def _reason_entry(value_id: str = "b", **extra: Any) -> dict[str, Any]:
    entry = {
        "id": value_id,
        "not_expressible": "No metric counts transaction rows.",
        "disposition": "candidate_catalog_change",
    }
    entry.update(extra)
    return entry


def _doc(*entries: dict[str, Any], **top: Any) -> dict[str, Any]:
    doc = {"version": 1, "golden_capture_date": CAPTURE_DATE, "values": list(entries)}
    doc.update(top)
    return doc


def _parse(doc: Any, *ids: str):
    return parse_mapping(doc, _golden(*(ids or ("a", "b"))))


def _golden_file(tmp_path: Path, **overrides: Any) -> Path:
    data = {
        "schema_version": 1,
        "capture_date_utc": CAPTURE_DATE,
        "values": [{"id": "a", "tag": "owner_api", "value": "1.00"}],
    }
    data.update(overrides)
    path = tmp_path / "golden.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _golden_with_value(tmp_path: Path, **value: Any) -> Path:
    base = {"id": "a", "tag": "owner_api", "value": "1.00"}
    base.update(value)
    return _golden_file(tmp_path, values=[base])


def test_reproduce_real_files_load_in_golden_order():
    golden = load_golden()
    mapping = load_mapping()

    assert len(mapping.entries) == 48
    assert [entry.id for entry in mapping.entries] == [value.id for value in golden.values]
    assert mapping.golden_capture_date == golden.capture_date
    mapped = [e for e in mapping.entries if isinstance(e, MappedQuery)]
    not_expressible = [e for e in mapping.entries if isinstance(e, NotExpressible)]
    assert (len(mapped), len(not_expressible)) == (44, 4)
    assert all(isinstance(entry.query, StructuredQuery) for entry in mapped)


def test_reproduce_real_files_have_exactly_one_known_difference():
    mapping = load_mapping()

    known = [e.id for e in mapping.entries if isinstance(e, MappedQuery) and e.known_difference]

    assert known == ["obligations.recipient.Z4XGALW7L9K6"]


def test_reproduce_default_paths_exist():
    assert DEFAULT_GOLDEN.is_file()
    assert DEFAULT_MAPPING.is_file()


def test_reproduce_small_document_loads_both_kinds_in_golden_order():
    mapping = _parse(_doc(_reason_entry("b"), _query_entry("a")))

    assert [e.id for e in mapping.entries] == ["a", "b"]
    assert isinstance(mapping.by_id()["a"], MappedQuery)
    assert isinstance(mapping.by_id()["b"], NotExpressible)
    assert mapping.by_id()["a"].row == {}


def test_reproduce_load_mapping_reads_a_yaml_file(tmp_path):
    path = tmp_path / "mapping.yaml"
    path.write_text(yaml.safe_dump(_doc(_query_entry("a"))), encoding="utf-8")

    mapping = load_mapping(path, _golden("a"))

    assert [e.id for e in mapping.entries] == ["a"]


def test_reproduce_golden_id_missing_from_mapping_is_named():
    with pytest.raises(MappingError, match="not mapped: b"):
        _parse(_doc(_query_entry("a")))


def test_reproduce_id_mapped_twice_is_refused():
    with pytest.raises(MappingError, match="mapped twice"):
        _parse(_doc(_query_entry("a"), _query_entry("a"), _reason_entry("b")))


def test_reproduce_id_that_is_not_a_golden_id_is_refused():
    with pytest.raises(MappingError, match="'zzz' is not a golden id"):
        _parse(_doc(_query_entry("a"), _reason_entry("b"), _query_entry("zzz")))


def test_reproduce_entry_with_query_and_not_expressible_is_refused():
    entry = _query_entry("a", not_expressible="a reason")
    with pytest.raises(MappingError, match="exactly one of query and not_expressible"):
        _parse(_doc(entry), "a")


def test_reproduce_entry_with_neither_query_nor_not_expressible_is_refused():
    with pytest.raises(MappingError, match="exactly one of query and not_expressible"):
        _parse(_doc({"id": "a"}), "a")


def test_reproduce_entry_that_is_not_a_mapping_is_refused():
    with pytest.raises(MappingError, match="expected a mapping"):
        _parse(_doc("a"), "a")  # type: ignore[arg-type]


@pytest.mark.parametrize("value_id", [None, "", 7])
def test_reproduce_entry_id_must_be_a_non_empty_string(value_id):
    with pytest.raises(MappingError, match="id must be a non-empty string"):
        _parse(_doc({"id": value_id, "query": QUERY}), "a")


def test_reproduce_unknown_key_on_query_entry_is_refused():
    with pytest.raises(MappingError, match=r"unknown keys \['colour'\]"):
        _parse(_doc(_query_entry("a", colour="red")), "a")


def test_reproduce_not_expressible_key_on_query_entry_is_refused():
    with pytest.raises(MappingError, match=r"unknown keys \['disposition'\]"):
        _parse(_doc(_query_entry("a", disposition="out_of_scope_by_design")), "a")


def test_reproduce_unknown_key_on_not_expressible_entry_is_refused():
    with pytest.raises(MappingError, match=r"unknown keys \['row'\]"):
        _parse(_doc(_reason_entry("a", row={})), "a")


def test_reproduce_unknown_top_level_key_is_refused():
    with pytest.raises(MappingError, match=r"unknown top-level keys \['extra'\]"):
        _parse(_doc(_query_entry("a"), extra=1), "a")


def test_reproduce_top_level_that_is_not_a_mapping_is_refused():
    with pytest.raises(MappingError, match="top level"):
        _parse([_query_entry("a")], "a")


def test_reproduce_values_that_is_not_a_list_is_refused():
    with pytest.raises(MappingError, match="values must be a list"):
        _parse({"version": 1, "golden_capture_date": CAPTURE_DATE, "values": {}}, "a")


@pytest.mark.parametrize("version", [0, 2, "1", None])
def test_reproduce_wrong_mapping_version_is_refused(version):
    with pytest.raises(MappingError, match="version must be 1"):
        _parse(_doc(_query_entry("a"), version=version), "a")


def test_reproduce_capture_date_different_from_golden_is_refused():
    with pytest.raises(MappingError, match="golden_capture_date '2026-10-31'"):
        _parse(_doc(_query_entry("a"), golden_capture_date="2026-10-31"), "a")


def test_reproduce_query_with_extra_field_names_the_id():
    entry = _query_entry("a")
    entry["query"]["colour"] = "red"
    with pytest.raises(MappingError, match=r"\(a\).*not a structured query.*colour"):
        _parse(_doc(entry), "a")


def test_reproduce_filter_without_value_names_the_id():
    entry = {"id": "a", "query": {"metric": "obligations", "filters": [{"field": "f", "op": "eq"}]}}
    with pytest.raises(MappingError, match=r"\(a\).*filters\[0\]\.value"):
        _parse(_doc(entry), "a")


def test_reproduce_query_with_bad_time_range_is_a_mapping_error():
    entry = {
        "id": "a",
        "query": {"metric": "obligations", "time_range": {"start": "x", "end": "y"}},
    }
    with pytest.raises(MappingError, match=r"\(a\)"):
        _parse(_doc(entry), "a")


def test_reproduce_query_that_is_not_an_object_is_a_mapping_error():
    with pytest.raises(MappingError, match=r"\(a\)"):
        _parse(_doc({"id": "a", "query": "obligations"}), "a")


@pytest.mark.parametrize("reason", ["", "   ", None, 3])
def test_reproduce_not_expressible_empty_or_not_text_is_refused(reason):
    entry = _reason_entry("a", not_expressible=reason)
    with pytest.raises(MappingError, match="not_expressible must be non-empty text"):
        _parse(_doc(entry), "a")


def test_reproduce_not_expressible_over_two_lines_is_refused():
    entry = _reason_entry("a", not_expressible="first line\nsecond line")
    with pytest.raises(MappingError, match="one-line reason"):
        _parse(_doc(entry), "a")


def test_reproduce_not_expressible_is_stripped_to_one_line():
    entry = _reason_entry("a", not_expressible="  one line\n")
    mapping = _parse(_doc(entry), "a")

    assert mapping.by_id()["a"].reason == "one line"


def test_reproduce_disposition_missing_is_refused():
    entry = _reason_entry("a")
    del entry["disposition"]
    with pytest.raises(MappingError, match="disposition must be one of"):
        _parse(_doc(entry), "a")


@pytest.mark.parametrize("disposition", ["later", "", None, "OUT_OF_SCOPE_BY_DESIGN"])
def test_reproduce_disposition_outside_the_two_allowed_is_refused(disposition):
    with pytest.raises(MappingError, match="candidate_catalog_change, out_of_scope_by_design"):
        _parse(_doc(_reason_entry("a", disposition=disposition)), "a")


@pytest.mark.parametrize("disposition", ["candidate_catalog_change", "out_of_scope_by_design"])
def test_reproduce_both_allowed_dispositions_load(disposition):
    mapping = _parse(_doc(_reason_entry("a", disposition=disposition)), "a")

    assert mapping.by_id()["a"].disposition == disposition


def test_reproduce_attempt_with_both_keys_loads():
    attempt = {"query": {"metric": "transaction_count"}, "refusal": "unknown_metric"}
    mapping = _parse(_doc(_reason_entry("a", attempt=attempt)), "a")

    loaded = mapping.by_id()["a"].attempt
    assert loaded.query == {"metric": "transaction_count"}
    assert loaded.refusal == "unknown_metric"


def test_reproduce_attempt_is_optional():
    assert _parse(_doc(_reason_entry("a")), "a").by_id()["a"].attempt is None


@pytest.mark.parametrize(
    "attempt",
    [
        {"query": {"metric": "m"}},
        {"refusal": "unknown_metric"},
        {"query": {"metric": "m"}, "refusal": "unknown_metric", "extra": 1},
        "unknown_metric",
        [],
    ],
    ids=["missing_refusal", "missing_query", "extra_key", "text", "list"],
)
def test_reproduce_attempt_with_wrong_keys_is_refused(attempt):
    with pytest.raises(MappingError, match="exactly query and refusal"):
        _parse(_doc(_reason_entry("a", attempt=attempt)), "a")


@pytest.mark.parametrize("refusal", ["", None, 4, ["unknown_metric"]])
def test_reproduce_attempt_refusal_empty_or_not_text_is_refused(refusal):
    attempt = {"query": {"metric": "m"}, "refusal": refusal}
    with pytest.raises(MappingError, match="attempt.refusal must be a refusal code"):
        _parse(_doc(_reason_entry("a", attempt=attempt)), "a")


def test_reproduce_attempt_query_that_is_not_a_mapping_is_refused():
    attempt = {"query": "metric", "refusal": "unknown_metric"}
    with pytest.raises(MappingError, match="attempt.query must be a mapping"):
        _parse(_doc(_reason_entry("a", attempt=attempt)), "a")


@pytest.mark.parametrize("row", [["a", "b"], "recipient", 3, None])
def test_reproduce_row_that_is_not_a_mapping_is_refused(row):
    with pytest.raises(MappingError, match="row must be a mapping"):
        _parse(_doc(_query_entry("a", row=row)), "a")


@pytest.mark.parametrize("selector", [["x"], 1.5, {"k": "v"}], ids=["list", "float", "mapping"])
def test_reproduce_row_value_of_wrong_type_is_refused(selector):
    with pytest.raises(MappingError, match="row.recipient must be text"):
        _parse(_doc(_query_entry("a", row={"recipient": selector})), "a")


def test_reproduce_row_key_that_is_not_text_is_refused():
    with pytest.raises(MappingError, match="row keys must be output column names"):
        _parse(_doc(_query_entry("a", row={1: "x"})), "a")


def test_reproduce_row_accepts_text_integer_boolean_and_null():
    row = {"name": "EPA", "fy": 2025, "flag": True, "blank": None}
    mapping = _parse(_doc(_query_entry("a", row=row)), "a")

    assert mapping.by_id()["a"].row == row


def test_reproduce_known_difference_and_note_are_stripped():
    entry = _query_entry("a", known_difference="  differs by one cent \n", note="\n why  ")
    mapped = _parse(_doc(entry), "a").by_id()["a"]

    assert mapped.known_difference == "differs by one cent"
    assert mapped.note == "why"


def test_reproduce_known_difference_and_note_default_to_none():
    mapped = _parse(_doc(_query_entry("a")), "a").by_id()["a"]

    assert mapped.known_difference is None
    assert mapped.note is None


@pytest.mark.parametrize("blank", ["", "  \n ", None, 5])
def test_reproduce_blank_known_difference_is_refused(blank):
    with pytest.raises(MappingError, match="known_difference must be non-empty text"):
        _parse(_doc(_query_entry("a", known_difference=blank)), "a")


def test_reproduce_blank_note_is_refused():
    with pytest.raises(MappingError, match="note must be non-empty text"):
        _parse(_doc(_query_entry("a", note="  ")), "a")


def test_reproduce_note_on_not_expressible_entry_is_kept():
    mapped = _parse(_doc(_reason_entry("a", note=" because ")), "a").by_id()["a"]

    assert mapped.note == "because"


def test_reproduce_load_golden_reads_a_small_file(tmp_path):
    path = _golden_file(
        tmp_path,
        values=[
            {"id": "a", "tag": "owner_api", "value": "1.00", "unit": "usd", "description": "d"},
            {"id": "b", "tag": "derived", "value": 12},
        ],
    )

    golden = load_golden(path)

    assert golden.capture_date == CAPTURE_DATE
    assert [(v.id, v.tag, v.value) for v in golden.values] == [
        ("a", "owner_api", "1.00"),
        ("b", "derived", 12),
    ]
    assert golden.values[1].unit == ""


def test_reproduce_load_golden_real_file_values_are_strings_or_integers():
    golden = load_golden()

    assert len(golden.values) == 48
    assert all(type(v.value) in (str, int) for v in golden.values)
    assert {v.tag for v in golden.values} == {"owner_api", "derived"}


@pytest.mark.parametrize("version", [0, 2, "1", None])
def test_reproduce_load_golden_wrong_schema_version_is_refused(tmp_path, version):
    with pytest.raises(MappingError, match="schema_version 1"):
        load_golden(_golden_file(tmp_path, schema_version=version))


def test_reproduce_load_golden_top_level_list_is_refused(tmp_path):
    path = tmp_path / "golden.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(MappingError, match="schema_version 1"):
        load_golden(path)


def test_reproduce_load_golden_missing_capture_date_is_refused(tmp_path):
    with pytest.raises(MappingError, match="capture_date_utc is missing"):
        load_golden(_golden_file(tmp_path, capture_date_utc=None))


@pytest.mark.parametrize("values", [[], {}, None, "x"])
def test_reproduce_load_golden_values_must_be_a_non_empty_list(tmp_path, values):
    with pytest.raises(MappingError, match="non-empty list"):
        load_golden(_golden_file(tmp_path, values=values))


def test_reproduce_load_golden_entry_that_is_not_an_object_is_refused(tmp_path):
    with pytest.raises(MappingError, match=r"values\[0\]: expected an object"):
        load_golden(_golden_file(tmp_path, values=["a"]))


@pytest.mark.parametrize("value_id", [None, "", 3])
def test_reproduce_load_golden_id_must_be_a_non_empty_string(tmp_path, value_id):
    with pytest.raises(MappingError, match="id must be a non-empty string"):
        load_golden(_golden_with_value(tmp_path, id=value_id))


def test_reproduce_load_golden_duplicated_id_is_refused(tmp_path):
    entry = {"id": "a", "tag": "owner_api", "value": "1.00"}
    with pytest.raises(MappingError, match="'a' appears twice"):
        load_golden(_golden_file(tmp_path, values=[entry, dict(entry)]))


def test_reproduce_load_golden_float_value_is_refused(tmp_path):
    with pytest.raises(MappingError, match="decimal string or an integer"):
        load_golden(_golden_with_value(tmp_path, value=0.449020))


def test_reproduce_load_golden_boolean_value_is_refused(tmp_path):
    with pytest.raises(MappingError, match="decimal string or an integer"):
        load_golden(_golden_with_value(tmp_path, value=True))


@pytest.mark.parametrize("value", [None, [1], {"v": 1}])
def test_reproduce_load_golden_other_value_types_are_refused(tmp_path, value):
    with pytest.raises(MappingError, match="decimal string or an integer"):
        load_golden(_golden_with_value(tmp_path, value=value))


@pytest.mark.parametrize("tag", ["manual", "", None, "OWNER_API"])
def test_reproduce_load_golden_tag_outside_owner_api_or_derived_is_refused(tmp_path, tag):
    with pytest.raises(MappingError, match="tag must be one of owner_api, derived"):
        load_golden(_golden_with_value(tmp_path, tag=tag))
