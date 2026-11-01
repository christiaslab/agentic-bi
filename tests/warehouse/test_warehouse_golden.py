"""The star schema reproduces the snapshot's totals and the golden values, to the cent.

Every query reads the contract tables only, through their joins, with bound values.
"""

from __future__ import annotations

import json
import re
from decimal import ROUND_HALF_EVEN, Decimal

import pytest

from agentic_bi import warehouse

GOLDEN = warehouse.ROOT / "docs" / "golden" / "golden.json"
_VALUES = json.loads(GOLDEN.read_text(encoding="utf-8"))["values"]

# golden dimension -> the query over the star schema that gives its obligations
BY_DIMENSION = {
    "fiscal_month": (
        "select sum(f.obligation_amount) from fct_contract_transaction f "
        'join dim_date d on d."date" = f.action_date '
        "where d.fiscal_year = ? and d.fiscal_month = ?",
        ("fiscal_year", "fiscal_month"),
    ),
    "recipient_uei": (
        "select sum(f.obligation_amount) from fct_contract_transaction f "
        "join dim_recipient r on r.recipient_uei = f.recipient_uei where r.recipient_uei = ?",
        ("recipient_uei",),
    ),
    "psc_code": (
        "select sum(f.obligation_amount) from fct_contract_transaction f "
        "join dim_psc p on p.psc_code = f.psc_code where p.psc_code = ?",
        ("psc_code",),
    ),
    "naics_code": (
        "select sum(f.obligation_amount) from fct_contract_transaction f "
        "join dim_naics n on n.naics_code = f.naics_code where n.naics_code = ?",
        ("naics_code",),
    ),
    "sub_agency_name": (
        "select sum(f.obligation_amount) from fct_contract_transaction f "
        "join dim_sub_agency s on s.sub_agency_code = f.sub_agency_code "
        "where s.sub_agency_name = ?",
        ("sub_agency_name",),
    ),
    "pop_state_code": (
        "select sum(f.obligation_amount) from fct_contract_transaction f "
        "where f.pop_state_code = ?",
        ("pop_state_code",),
    ),
}


def _dimension(value: dict) -> str | None:
    found = [name for name in BY_DIMENSION if name in value["dimensions"]]
    return found[0] if found else None


DIMENSIONAL = [v for v in _VALUES if v["tag"] == "owner_api" and _dimension(v)]


def test_row_count_and_obligation_sum_match_the_manifest(con, manifest: dict) -> None:
    rows, total = con.execute(
        "select count(*), sum(obligation_amount) from fct_contract_transaction"
    ).fetchone()
    assert rows == manifest["row_count"]
    assert f"{total}" == manifest["obligation_sum"]


def test_golden_values_cover_fiscal_months_and_two_more_dimensions() -> None:
    covered = {_dimension(value) for value in DIMENSIONAL}
    assert "fiscal_month" in covered
    assert len(covered) >= 3
    assert len([v for v in DIMENSIONAL if _dimension(v) == "fiscal_month"]) == 12


@pytest.mark.parametrize("value", DIMENSIONAL, ids=[v["id"] for v in DIMENSIONAL])
def test_golden_value_reproduces_through_its_dimension(con, value: dict) -> None:
    sql, parameters = BY_DIMENSION[_dimension(value)]
    result = con.execute(sql, [value["dimensions"][name] for name in parameters]).fetchone()[0]
    assert f"{result}" == value["value"]


def test_top10_reporting_share_matches_the_derived_golden_value(con, golden_values) -> None:
    value = golden_values["recipients.top10_reporting_share"]
    top10, total = con.execute(
        "with by_reporting as ("
        "  select r.reporting_uei, sum(f.obligation_amount) as net"
        "  from fct_contract_transaction f"
        "  join dim_reporting_recipient r on r.reporting_uei = f.reporting_uei"
        "  group by r.reporting_uei"
        "), ranked as ("
        "  select net, row_number() over (order by net desc, reporting_uei) as rank"
        "  from by_reporting"
        ") "
        "select (select sum(net) from ranked where rank <= 10), "
        "(select sum(obligation_amount) from fct_contract_transaction)"
    ).fetchone()
    # the formula records the numerator and the denominator to the cent
    numerator, denominator = re.findall(r"-?\d+\.\d{2}\b", value["derivation"]["formula"])
    assert (f"{top10}", f"{total}") == (numerator, denominator)
    share = (top10 / total).quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)
    assert f"{share}" == value["value"]


def test_obligations_by_competition_code_match_the_derived_golden_values(con) -> None:
    golden = {
        v["dimensions"]["competition_code"]: v["value"]
        for v in _VALUES
        if v["tag"] == "derived" and "competition_code" in v["dimensions"]
    }
    built = dict(
        con.execute(
            "select c.competition_code, sum(f.obligation_amount) from fct_contract_transaction f "
            "join dim_competition c on c.competition_code = f.competition_code "
            "group by c.competition_code"
        ).fetchall()
    )
    unreported = con.execute(
        "select sum(obligation_amount) from fct_contract_transaction where competition_code is null"
    ).fetchone()[0]
    assert {code: f"{amount}" for code, amount in built.items()} | {None: f"{unreported}"} == golden


def test_the_seed_lists_the_competed_codes_of_the_golden_values(con) -> None:
    golden_codes = {
        v["dimensions"]["competition_code"]
        for v in _VALUES
        if "competition_code" in v["dimensions"] and v["dimensions"]["competition_code"]
    }
    seed = dict(
        con.execute(
            "select competition_code, competition_description from seeds.extent_competed"
        ).fetchall()
    )
    built = dict(
        con.execute(
            "select competition_code, competition_description from dim_competition"
        ).fetchall()
    )
    assert set(seed) == golden_codes == set(built)
    assert seed == built  # the seed's labels are the publisher's


def test_is_competed_follows_the_publisher_label(con) -> None:
    rows = con.execute(
        "select competition_code, competition_description, is_competed from dim_competition"
    ).fetchall()
    assert rows
    for code, label, is_competed in rows:
        assert is_competed is not label.startswith("NOT "), code
