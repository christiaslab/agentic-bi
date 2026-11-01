"""The slice filter and the column allow-list of ADR-0001."""

from pathlib import Path

from agentic_bi.ingest import spec

ROOT = Path(__file__).resolve().parents[2]

# Every excluded field of the 297-column contract transaction download recorded on
# 2026-11-01 (ADR-0001 point 6).
EXCLUDED_SOURCE_FIELDS = [
    "recipient_phone_number",
    "recipient_fax_number",
    "recipient_address_line_1",
    "recipient_address_line_2",
    *(f"highly_compensated_officer_{n}_{part}" for n in range(1, 6) for part in ("name", "amount")),
]


def test_allow_list_holds_no_excluded_field() -> None:
    assert spec.excluded_in(spec.FIELDS) == []


def test_exclusion_rule_catches_every_excluded_source_field() -> None:
    assert spec.excluded_in(EXCLUDED_SOURCE_FIELDS) == EXCLUDED_SOURCE_FIELDS


def test_allow_list_is_unique_and_typed_fields_are_in_it() -> None:
    assert len(set(spec.FIELDS)) == len(spec.FIELDS)
    typed = spec.DECIMAL_FIELDS | spec.DATE_FIELDS | spec.INTEGER_FIELDS
    assert typed <= set(spec.FIELDS)


def test_filter_is_the_adr_slice_and_a_fresh_copy() -> None:
    first = spec.filters()
    assert first["award_type_codes"] == ["A", "B", "C", "D"]
    assert first["time_period"] == [
        {"start_date": "2024-10-01", "end_date": "2025-09-30", "date_type": "action_date"}
    ]
    assert first["agencies"] == [
        {"type": "awarding", "tier": "toptier", "name": "Environmental Protection Agency"}
    ]
    first["award_type_codes"].append("IDV_A")
    assert spec.filters()["award_type_codes"] == ["A", "B", "C", "D"]


def test_every_kept_field_is_documented_in_sources_md() -> None:
    text = (ROOT / "docs" / "sources.md").read_text(encoding="utf-8")
    missing = [name for name in spec.FIELDS if f"`{name}`" not in text]
    assert missing == []
