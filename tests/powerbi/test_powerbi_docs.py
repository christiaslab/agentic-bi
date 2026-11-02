"""docs/powerbi.md: the golden check table, its values from the committed data, and the names."""

from __future__ import annotations

from decimal import Decimal

import duckdb
import pytest

from agentic_bi.powerbi import ROOT, generate
from agentic_bi.powerbi.dax import DEFAULT_DAX_PATH
from agentic_bi.powerbi.schema import load_schema
from agentic_bi.semantic import compile, default_catalog

GUIDE = ROOT / "docs" / "powerbi.md"
DATA = ROOT / "data" / "model"
HEADER = "| # | Golden id | Tag | Measure | Filter in Desktop | Golden value | Desktop value |"
# The structured query that gives each checked value from the contract tables, as the agent
# would ask it. Recipient values are the publisher's, by the recipient's own UEI, which the
# catalog does not expose as a dimension, so that one is read by a plain query.
QUERIES = {
    "obligations.total": {"metric": "obligations"},
    "obligations.fiscal_month.04": {"metric": "obligations", "grain": "fiscal_month"},
    "awards.average_net_obligation": {"metric": "avg_award_value"},
    "recipients.top10_reporting_share": {"metric": "supplier_concentration_top10"},
}


def _check_table() -> list[list[str]]:
    text = GUIDE.read_text(encoding="utf-8")
    assert HEADER in text
    rows = text.split(HEADER, 1)[1].split("\n\n", 1)[0].strip().splitlines()[1:]
    return [[cell.strip() for cell in row.strip().strip("|").split("|")] for row in rows]


@pytest.fixture(scope="module")
def data_connection():
    connection = duckdb.connect()
    for table in load_schema().tables:
        connection.execute(
            f'CREATE TABLE "{table.name}" AS SELECT * FROM read_parquet(?)',
            [str(DATA / f"{table.name}.parquet")],
        )
    yield connection
    connection.close()


def test_the_check_table_has_five_golden_values_of_the_required_kinds(golden_values) -> None:
    rows = _check_table()
    assert len(rows) == 5
    ids = [row[1].strip("`") for row in rows]
    assert len(set(ids)) == 5
    for row, golden_id in zip(rows, ids, strict=True):
        value = golden_values[golden_id]
        assert row[2] == value["tag"], golden_id
        assert row[5] == str(value["value"]), golden_id
        assert row[6] == "", "the Desktop value column is the owner's to fill"
        assert row[3].strip("`") in default_catalog().metrics
    chosen = [golden_values[golden_id] for golden_id in ids]
    assert any(value["tag"] == "derived" for value in chosen)
    assert any("fiscal_month" in value["dimensions"] for value in chosen)
    assert any("recipient_uei" in value["dimensions"] for value in chosen)


def test_the_committed_data_gives_each_checked_value(golden_values, data_connection) -> None:
    for row in _check_table():
        golden_id = row[1].strip("`")
        golden = golden_values[golden_id]
        expected = Decimal(str(golden["value"]))
        if golden_id in QUERIES:
            query = QUERIES[golden_id]
            assert query["metric"] == row[3].strip("`")
            compiled = compile(query)
            result = data_connection.execute(compiled.sql, compiled.params).fetchall()
            if "fiscal_month" in golden["dimensions"]:
                wanted = (golden["dimensions"]["fiscal_year"], golden["dimensions"]["fiscal_month"])
                result = [r[2:] for r in result if r[:2] == wanted]
            (value,) = result[0]
        else:
            assert "recipient_uei" in golden["dimensions"]
            value = data_connection.execute(
                "SELECT sum(obligation_amount) FROM fct_contract_transaction "
                "WHERE recipient_uei = ?",
                [golden["dimensions"]["recipient_uei"]],
            ).fetchone()[0]
        places = -expected.as_tuple().exponent
        assert round(Decimal(str(value)), places) == expected, golden_id


def test_the_guide_names_the_commands_the_parameter_and_every_metric() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    assert "uv run python -m agentic_bi.powerbi.export --out data/model" in text
    assert "uv run python -m agentic_bi.powerbi.generate --out powerbi" in text
    assert f"`{generate.PARAMETER}`" in text
    for name in default_catalog().metrics:
        assert f"`{name}`" in text, name


def test_the_guide_states_the_committed_data_size() -> None:
    total = sum(path.stat().st_size for path in DATA.glob("*.parquet"))
    assert f"{total:,} bytes" in GUIDE.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", [GUIDE, DEFAULT_DAX_PATH])
def test_no_em_or_en_dash(path) -> None:
    text = path.read_text(encoding="utf-8")
    assert chr(0x2014) not in text and chr(0x2013) not in text
