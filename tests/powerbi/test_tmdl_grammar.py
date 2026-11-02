"""Check the committed TMDL with an independent grammar checker (tmdl_grammar.py).

The negative tests change a copy of the committed text in memory. Nothing is written to disk.
"""

from pathlib import Path

import pytest
from tmdl_grammar import (
    check_model,
    dax_references,
    load_definition,
    parse_column_ref,
    parse_document,
    quote_name,
    read_name,
)

DEFINITION = (
    Path(__file__).resolve().parents[2] / "powerbi" / "AgenticBI.SemanticModel" / "definition"
)
DATE = "tables/dim_date.tmdl"
FACT = "tables/fct_contract_transaction.tmdl"
RELATIONSHIPS = "relationships.tmdl"
GUID = "00000000-0000-4000-8000-000000000001"


@pytest.fixture(scope="module")
def committed() -> dict[str, str]:
    return load_definition(DEFINITION)


def mutate(files: dict[str, str], path: str, old: str, new: str, count: int = 1) -> dict[str, str]:
    """Return a copy of files with `old` replaced by `new` in one file.

    Write old and new with \\n; they are turned into the CRLF of the committed files. The old
    text must occur, so a test cannot pass because a mutation changed nothing.
    """
    text = files[path]
    if "\r\n" in text:
        old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
    assert old in text, f"{old!r} not found in {path}"
    mutated = dict(files)
    mutated[path] = text.replace(old, new, count)
    return mutated


def assert_fails(files: dict[str, str], *words: str) -> list[str]:
    errors = check_model(files)
    assert errors, "the checker accepted a broken model"
    for word in words:
        assert any(word in error for error in errors), f"no error mentions {word!r}: {errors}"
    return errors


# --------------------------------------------------------------------------- committed files


def test_committed_files_pass(committed):
    assert check_model(committed) == []


def test_all_expected_files_are_checked(committed):
    names = set(committed)
    assert {"database.tmdl", "model.tmdl", "expressions.tmdl", RELATIONSHIPS} <= names
    assert len([n for n in names if n.startswith("tables/")]) == 8


def test_committed_files_use_crlf_and_no_leading_space(committed):
    for path, text in committed.items():
        assert "\r\n" in text and "\n" not in text.replace("\r\n", ""), path
        assert not any(line.startswith(" ") for line in text.split("\r\n")), path


def test_committed_files_have_no_dash_punctuation(committed):
    dashes = (chr(0x2014), chr(0x2013))
    for path, text in committed.items():
        assert not any(dash in text for dash in dashes), path


def test_lf_line_endings_pass_too(committed):
    lf = {path: text.replace("\r\n", "\n") for path, text in committed.items()}
    assert check_model(lf) == []


def test_parser_reads_the_object_tree(committed):
    roots, errors = parse_document(committed[FACT], "table", FACT)
    assert errors == []
    (table,) = roots
    assert table.name == "fct_contract_transaction"
    assert [m.name for m in table.kids("measure")][:2] == ["obligations", "award_count"]
    assert table.kids("measure")[0].description.startswith("Net federal action obligations")
    concentration = table.kids("measure")[3]
    assert concentration.value is None and concentration.body[0] == "VAR _per_recipient ="
    assert concentration.body[1].startswith("    ADDCOLUMNS")
    assert table.kids("partition")[0].props["source"].body[0] == "let"


def test_model_level_annotation_at_depth_zero_is_accepted(committed):
    roots, errors = parse_document(committed["model.tmdl"], "model", "model.tmdl")
    assert errors == []
    assert roots[0].children[0].root_level and roots[0].children[0].name.startswith("__PBI")


def test_annotation_before_the_model_is_refused():
    text = "annotation a = 1\nmodel Model\n\tculture: en-US\n"
    _, errors = parse_document(text, "model", "model.tmdl")
    assert any("top of a model file" in e for e in errors)


# --------------------------------------------------------------------------- names and quotes


def test_unquoted_name_with_a_space_fails(committed):
    errors = assert_fails(mutate(committed, DATE, "\tcolumn fiscal_month", "\tcolumn Fiscal Month"))
    assert any("single quotes" in error for error in errors)


def test_unquoted_name_with_a_dot_fails(committed):
    assert_fails(mutate(committed, DATE, "\tcolumn fiscal_month", "\tcolumn fiscal.month"))


def test_unquoted_table_name_with_a_space_fails(committed):
    assert_fails(mutate(committed, DATE, "table dim_date", "table dim date"), "single quotes")


def test_unquoted_part_of_a_column_reference_fails(committed):
    broken = mutate(
        committed,
        RELATIONSHIPS,
        "fromColumn: fct_contract_transaction.recipient_uei",
        "fromColumn: fct_contract_transaction.Recipient UEI",
    )
    assert_fails(broken, "single quotes")


def test_quoted_column_name_with_a_space_passes(committed):
    renamed = mutate(committed, DATE, "\tcolumn fiscal_month", "\tcolumn 'Fiscal Month'")
    assert check_model(renamed) == []


def test_renamed_column_used_in_dax_passes_when_quoted_correctly(committed):
    renamed = mutate(committed, FACT, "\tcolumn obligation_amount", "\tcolumn 'Obligation Amount'")
    renamed = mutate(
        renamed,
        FACT,
        "SUM ( fct_contract_transaction[obligation_amount] )",
        "SUM ( fct_contract_transaction[Obligation Amount] )",
    )
    assert check_model(renamed) == []


def test_renamed_column_used_in_dax_fails_when_dax_is_not_updated(committed):
    renamed = mutate(committed, FACT, "\tcolumn obligation_amount", "\tcolumn 'Obligation Amount'")
    assert_fails(renamed, "obligation_amount")


def test_renamed_column_used_in_a_relationship_passes_when_quoted_correctly(committed):
    renamed = mutate(committed, FACT, "\tcolumn recipient_uei", "\tcolumn 'Recipient UEI'")
    renamed = mutate(
        renamed,
        RELATIONSHIPS,
        "fromColumn: fct_contract_transaction.recipient_uei",
        "fromColumn: fct_contract_transaction.'Recipient UEI'",
    )
    assert check_model(renamed) == []


def test_renamed_table_used_in_dax_passes_when_quoted_correctly(committed):
    renamed = mutate(
        committed, "tables/dim_competition.tmdl", "table dim_competition", "table 'Dim Competition'"
    )
    renamed = mutate(
        renamed, RELATIONSHIPS, "toColumn: dim_competition.", "toColumn: 'Dim Competition'."
    )
    renamed = mutate(
        renamed, FACT, "KEEPFILTERS ( dim_competition[", "KEEPFILTERS ( 'Dim Competition'["
    )
    assert check_model(renamed) == []
    unquoted = mutate(renamed, FACT, "'Dim Competition'[", "Dim Competition[")
    assert_fails(unquoted)


def test_renamed_measure_used_by_other_measures_passes(committed):
    renamed = mutate(committed, FACT, "\tmeasure obligations =", "\tmeasure 'Net Obligations' =")
    renamed = mutate(renamed, FACT, "[obligations]", "[Net Obligations]", count=99)
    assert check_model(renamed) == []


def test_doubled_single_quote_is_one_quote():
    assert read_name("'It''s here' = 1") == ("It's here", 12)
    assert read_name("''''") == ("'", 4)
    assert quote_name("It's here") == "'It''s here'"
    assert quote_name("plain_name") == "plain_name"
    assert read_name(quote_name("a'b c"))[0] == "a'b c"
    assert parse_column_ref("'My ''T'''.'a b'") == ("My 'T'", "a b")


def test_doubled_single_quote_in_a_declared_name():
    text = f"table 'It''s here'\n\tlineageTag: {GUID}\n"
    (table,), errors = parse_document(text, "table", "t.tmdl")
    assert table.name == "It's here"
    assert all("single quotes" not in error for error in errors)


def test_open_quote_fails():
    _, errors = parse_document("table 'oops\n", "table", "t.tmdl")
    assert any("not closed" in e for e in errors)


# --------------------------------------------------------------------------- indentation


def test_space_instead_of_tab_fails(committed):
    broken = mutate(committed, DATE, "\t\tisKey\n", "\t isKey\n")
    assert_fails(broken, "tabs only")


def test_spaces_only_indentation_fails(committed):
    broken = mutate(committed, DATE, "\t\tisKey\n", "  isKey\n")
    assert_fails(broken, "tabs only")


def test_property_one_level_too_deep_fails(committed):
    broken = mutate(
        committed, DATE, "\t\tformatString: yyyy-mm-dd", "\t\t\tformatString: yyyy-mm-dd"
    )
    assert_fails(broken, "too deep")


def test_property_one_level_too_shallow_fails(committed):
    broken = mutate(committed, DATE, "\t\tsummarizeBy: none\n", "\tsummarizeBy: none\n")
    assert_fails(broken, "unknown property")


def test_object_at_the_wrong_depth_fails(committed):
    assert_fails(mutate(committed, DATE, "\tcolumn fiscal_year", "\t\tcolumn fiscal_year"))
    assert_fails(mutate(committed, DATE, "\tcolumn fiscal_year", "column fiscal_year"))


def test_expression_body_not_indented_deeper_fails(committed):
    broken = mutate(committed, DATE, "\t\t\t\tlet\n", "\t\t\tlet\n")
    assert_fails(broken)


def test_measure_body_needs_two_extra_tabs(committed):
    broken = mutate(committed, FACT, "\t\t\tVAR _per_recipient =\n", "\t\tVAR _per_recipient =\n")
    assert_fails(broken)


def test_measure_with_equals_and_no_body_fails(committed):
    broken = mutate(
        committed,
        FACT,
        "\tmeasure competition_rate =\n\t\t\tVAR",
        "\tmeasure competition_rate =\n\t\tformatString: 0\n\t\t\tVAR",
    )
    assert_fails(broken)


# --------------------------------------------------------------------------- keywords, properties


def test_unknown_property_fails(committed):
    broken = mutate(
        committed, DATE, "\t\tdataType: dateTime\n", "\t\tdataType: dateTime\n\t\tfooBar: 1\n"
    )
    assert_fails(broken, "unknown property 'fooBar'")


def test_misspelled_boolean_property_fails(committed):
    assert_fails(mutate(committed, DATE, "\t\tisKey\n", "\t\tisKeyy\n"), "unknown property")


def test_property_of_another_object_type_fails(committed):
    broken = mutate(
        committed, DATE, "\tdataCategory: Time\n", "\tdataCategory: Time\n\tsourceColumn: x\n"
    )
    assert_fails(broken, "unknown property 'sourceColumn' for a table")


def test_unknown_object_keyword_fails(committed):
    assert_fails(mutate(committed, DATE, "\tcolumn fiscal_year", "\tcolum fiscal_year"), "colum")
    assert_fails(mutate(committed, DATE, "\tpartition dim_date = m", "\tsegment dim_date = m"))


def test_object_not_allowed_under_its_parent_fails(committed):
    broken = mutate(
        committed,
        DATE,
        "\t\tannotation UnderlyingDateTimeDataType = Date\n",
        "\t\tmeasure inside_column = 1\n",
    )
    assert_fails(broken, "not allowed inside a column")


def test_invalid_property_values_fail(committed):
    assert_fails(mutate(committed, DATE, "dataType: dateTime", "dataType: datetime"), "dataType")
    assert_fails(
        mutate(committed, DATE, "summarizeBy: none", "summarizeBy: total", 1), "summarizeBy"
    )
    assert_fails(mutate(committed, DATE, "\t\tisKey\n", "\t\tisKey: yes\n"), "isKey")
    assert_fails(mutate(committed, DATE, "lineageTag: 343bce98", "lineageTag: nothex"), "GUID")
    assert_fails(mutate(committed, DATE, "mode: import", "mode: imported"), "mode")
    assert_fails(
        mutate(committed, DATE, "\t\tdataType: dateTime", "\t\tdataType:"), "needs a value"
    )


def test_partition_source_type_must_be_m(committed):
    assert_fails(mutate(committed, DATE, "partition dim_date = m", "partition dim_date = sql"))


def test_missing_required_parts_fail(committed):
    assert_fails(mutate(committed, DATE, "\t\tsourceColumn: calendar_year\n", ""), "sourceColumn")
    assert_fails(mutate(committed, RELATIONSHIPS, "\ttoColumn: dim_date.date\n", ""), "toColumn")


# --------------------------------------------------------------------------- descriptions


def test_description_followed_by_a_property_fails(committed):
    broken = mutate(
        committed, DATE, "\t\tdataType: dateTime\n", "\t\t/// note\n\t\tdataType: dateTime\n"
    )
    assert_fails(broken, "description")


def test_description_at_the_end_of_a_file_fails(committed):
    broken = dict(committed)
    broken[DATE] += "\t/// nothing below\r\n"
    assert_fails(broken, "end of the file")


def test_description_followed_by_a_blank_line_fails(committed):
    broken = mutate(committed, FACT, "\t/// Joins dim_psc\n", "\t/// Joins dim_psc\n\n")
    assert_fails(broken, "directly above")


def test_description_at_the_wrong_depth_fails(committed):
    broken = mutate(committed, FACT, "\t/// Joins dim_psc\n", "\t\t/// Joins dim_psc\n")
    assert_fails(broken, "depth")


def test_description_on_an_object_passes(committed):
    ok = mutate(
        committed, DATE, "\tcolumn fiscal_year\n", "\t/// The fiscal year\n\tcolumn fiscal_year\n"
    )
    assert check_model(ok) == []


# --------------------------------------------------------------------------- cross references


def test_relationship_to_a_missing_column_fails(committed):
    broken = mutate(committed, RELATIONSHIPS, "toColumn: dim_date.date", "toColumn: dim_date.dte")
    assert_fails(broken, "no column 'dte'")


def test_relationship_to_a_missing_table_fails(committed):
    broken = mutate(committed, RELATIONSHIPS, "toColumn: dim_date.date", "toColumn: dim_dates.date")
    assert_fails(broken, "unknown table 'dim_dates'")


def test_relationship_with_different_data_types_fails(committed):
    broken = mutate(
        committed, RELATIONSHIPS, "toColumn: dim_date.date", "toColumn: dim_date.fiscal_year"
    )
    assert_fails(broken, "data type")


def test_source_column_missing_from_the_m_selection_fails(committed):
    broken = mutate(committed, DATE, "\t\tsourceColumn: fiscal_year", "\t\tsourceColumn: fiscal_yr")
    assert_fails(broken, "fiscal_yr")


def test_m_selection_with_an_unused_column_fails(committed):
    broken = mutate(committed, DATE, '{"date", "fiscal_year",', '{"date", "extra", "fiscal_year",')
    assert_fails(broken, "extra")


def test_m_type_that_contradicts_the_data_type_fails(committed):
    broken = mutate(committed, DATE, '{"fiscal_year", Int64.Type}', '{"fiscal_year", type text}')
    assert_fails(broken, "does not match M type")


def test_partition_using_an_undeclared_parameter_fails(committed):
    broken = mutate(committed, DATE, "File.Contents(DataFolder &", "File.Contents(DataFolderX &")
    assert_fails(broken, "DataFolderX")


def test_partition_needs_the_expressions_file_for_its_parameter(committed):
    gone = dict(committed)
    del gone["expressions.tmdl"]
    assert_fails(gone, "DataFolder")


def test_measure_with_a_missing_column_fails(committed):
    broken = mutate(
        committed,
        FACT,
        "fct_contract_transaction[obligation_amount]",
        "fct_contract_transaction[obligation_amnt]",
    )
    assert_fails(broken, "obligation_amnt")


def test_measure_with_a_missing_table_fails(committed):
    broken = mutate(
        committed, FACT, "dim_competition[is_competed]", "dim_competitions[is_competed]"
    )
    assert_fails(broken, "dim_competitions")


def test_measure_with_a_missing_measure_fails(committed):
    broken = mutate(
        committed,
        FACT,
        "DIVIDE ( [obligations], [award_count] )",
        "DIVIDE ( [obligation], [award_count] )",
    )
    assert_fails(broken, "[obligation]")


def test_measure_with_a_missing_local_column_fails(committed):
    broken = mutate(
        committed, FACT, "SUMX ( _top_10, [@obligations] )", "SUMX ( _top_10, [@obligation] )"
    )
    assert_fails(broken, "[@obligation]")


def test_measure_may_use_a_local_column_added_in_the_same_expression(committed):
    roots, errors = parse_document(committed[FACT], "table", FACT)
    assert errors == []
    refs = dax_references(roots[0].kids("measure")[3].expression)
    assert "@obligations" in refs.strings and "@obligations" in refs.bare
    assert ("fct_contract_transaction", "reporting_uei") in refs.columns


def test_duplicate_measure_name_fails(committed):
    broken = mutate(committed, FACT, "\tmeasure award_count =", "\tmeasure obligations =")
    assert_fails(broken, "declared twice")


def test_measure_with_the_name_of_a_column_of_its_table_fails(committed):
    broken = mutate(committed, FACT, "\tmeasure award_count =", "\tmeasure award_key =")
    assert_fails(broken, "column's name")


def test_duplicate_column_name_fails(committed):
    broken = mutate(committed, DATE, "\tcolumn fiscal_year\n", "\tcolumn fiscal_quarter\n")
    assert_fails(broken, "declared twice")


def test_duplicate_lineage_tag_fails(committed):
    broken = mutate(
        committed,
        DATE,
        "lineageTag: 813f6cdd-18b9-5cd0-908f-f6e6672709a6",
        "lineageTag: 0e4ec6fa-e47e-527f-ab9f-468a1f65220d",
    )
    assert_fails(broken, "lineageTag")


# --------------------------------------------------------------------------- model facts


def test_two_time_tables_fail(committed):
    broken = mutate(
        committed, "tables/dim_psc.tmdl", "\tlineageTag: ", "\tdataCategory: Time\n\tlineageTag: "
    )
    assert_fails(broken, "exactly one table")


def test_no_time_table_fails(committed):
    assert_fails(mutate(committed, DATE, "\tdataCategory: Time\n", ""), "exactly one table")


def test_time_table_without_a_key_fails(committed):
    assert_fails(mutate(committed, DATE, "\t\tisKey\n", ""), "isKey")


def test_time_table_with_two_keys_fails(committed):
    broken = mutate(committed, DATE, "\t\tdataType: int64\n", "\t\tdataType: int64\n\t\tisKey\n")
    assert_fails(broken, "isKey")


def test_time_table_key_must_be_a_date(committed):
    broken = mutate(committed, DATE, "\t\tisKey\n", "")
    broken = mutate(broken, DATE, "\t\tdataType: int64\n", "\t\tdataType: int64\n\t\tisKey\n")
    assert_fails(broken, "dateTime")


def test_missing_model_file_fails(committed):
    gone = dict(committed)
    del gone["model.tmdl"]
    assert_fails(gone, "model.tmdl")


def test_unknown_file_fails(committed):
    assert_fails({**committed, "tables/notes.txt": ""}, "notes.txt")


def test_dax_reference_scanner():
    refs = dax_references(
        "CALCULATE ( SUM ( 'Sales Fact'[Net ''Amt] ), Dim[Key] = \"[not a ref]\" ) "
        "// ignored dim[comment]\n + [Rate] + VAR x = 1 RETURN [y]"
    )
    assert refs.columns == [("Sales Fact", "Net ''Amt"), ("Dim", "Key")]
    assert refs.bare == ["Rate", "y"]
    assert "[not a ref]" in refs.strings
