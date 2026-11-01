"""The model rules, proven on the synthetic snapshot built through the same project."""

from __future__ import annotations

import duckdb
import pytest
import synthetic_snapshot


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory: pytest.TempPathFactory):
    database = synthetic_snapshot.build(
        tmp_path_factory.mktemp("synthetic"), synthetic_snapshot.ROWS
    )
    con = duckdb.connect(str(database), read_only=True)
    yield con
    con.close()


def _pairs(con: duckdb.DuckDBPyConnection, sql: str) -> dict:
    return dict(con.execute(sql).fetchall())


def test_reporting_uei_falls_back_to_the_recipient_per_transaction(synthetic) -> None:
    built = _pairs(synthetic, "select transaction_key, reporting_uei from fct_contract_transaction")
    assert built == {"T01": "P1", "T02": "P1", "T03": "P1", "T04": "R3", "T05": "P2", "T06": "P3"}


def test_reporting_name_is_the_latest_and_the_greatest_key_breaks_a_tie(synthetic) -> None:
    built = _pairs(synthetic, "select reporting_uei, reporting_name from dim_reporting_recipient")
    assert built == {
        "P1": "PARENT ONE B",  # T02 and T03 share the latest day; T03 is the greater key
        "P2": "PARENT TWO",
        "P3": "PARENT THREE",
        "R3": "CHARLIE",  # the fallback takes the recipient's own name
    }


def test_one_row_per_recipient_with_its_latest_name(synthetic) -> None:
    built = _pairs(synthetic, "select recipient_uei, recipient_name from dim_recipient")
    assert built == {"R1": "ALPHA NEW", "R2": "BRAVO", "R3": "CHARLIE", "R4": "DELTA"}


def test_state_is_null_abroad_even_when_the_source_gives_one(synthetic) -> None:
    states = _pairs(
        synthetic, "select transaction_key, pop_state_code from fct_contract_transaction"
    )
    assert states["T04"] is None  # country CAN, source state ON
    assert {key for key, state in states.items() if state == "CA"} == set(states) - {"T04"}


def test_a_missing_competition_code_stays_null_with_no_dimension_row(synthetic) -> None:
    code = synthetic.execute(
        "select competition_code from fct_contract_transaction where transaction_key = 'T04'"
    ).fetchone()
    assert code == (None,)
    codes = synthetic.execute("select competition_code from dim_competition order by 1").fetchall()
    assert codes == [("A",), ("B",)]  # only the codes found; no row stands for a null


def test_dim_date_does_not_depend_on_the_transactions(synthetic) -> None:
    assert synthetic.execute("select count(*) from dim_date").fetchone()[0] == 365
