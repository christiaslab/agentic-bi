"""Every catalog metric, compiled and run on the synthetic fixture, against hand-added values.

The rows are listed in conftest.py. FY2025 below means time_range 2024-10-01 to 2025-09-30,
which holds every transaction except T16 (5000.00, fiscal year 2024).
"""

import datetime as dt
from decimal import Decimal

import pytest

FY2025 = {"start": "2024-10-01", "end": "2025-09-30"}


def d(value: str) -> Decimal:
    return Decimal(value)


# --- obligations: net sum, de-obligations negative -------------------------------------------


def test_obligations_fy2025_total_is_net(run) -> None:
    # T01..T15: 1000 + 500 + 300 + 200 - 450 + 1200 + 1100 + 1000 + 900 + 800 + 700 + 600
    # + 500 + 400 + 300 = 9050.00
    assert run({"metric": "obligations", "time_range": FY2025}) == [(d("9050.00"),)]


def test_obligations_without_time_range_covers_every_date_held(run) -> None:
    # 9050.00 + T16 5000.00
    assert run({"metric": "obligations"}) == [(d("14050.00"),)]


def test_obligations_by_fiscal_month_with_a_net_negative_month(run) -> None:
    rows = run({"metric": "obligations", "grain": "fiscal_month", "time_range": FY2025})
    assert rows == [
        (2025, 1, d("1300.00")),  # October 2024: T01 1000 + T03 300
        (2025, 2, d("800.00")),  # November: T02 500 + T15 300
        (2025, 3, d("200.00")),  # December: T04
        (2025, 4, d("1200.00")),  # January 2025: T06
        (2025, 5, d("-450.00")),  # February: T05, the de-obligation
        (2025, 6, d("1100.00")),  # March: T07
        (2025, 7, d("1000.00")),  # April: T08
        (2025, 8, d("900.00")),  # May: T09
        (2025, 9, d("800.00")),  # June: T10
        (2025, 10, d("700.00")),  # July: T11
        (2025, 11, d("600.00")),  # August: T12
        (2025, 12, d("900.00")),  # September: T13 500 + T14 400
    ]


def test_fiscal_year_boundary_puts_30_september_2024_in_fy2024(run) -> None:
    rows = run({"metric": "obligations", "grain": "fiscal_year"})
    assert rows == [(2024, d("5000.00")), (2025, d("9050.00"))]


def test_obligations_by_fiscal_quarter(run) -> None:
    rows = run({"metric": "obligations", "grain": "fiscal_quarter", "time_range": FY2025})
    # Q1 1300 + 800 + 200; Q2 1200 - 450 + 1100; Q3 1000 + 900 + 800; Q4 700 + 600 + 900
    assert rows == [
        (2025, 1, d("2300.00")),
        (2025, 2, d("1850.00")),
        (2025, 3, d("2700.00")),
        (2025, 4, d("2200.00")),
    ]


def test_net_negative_award_keeps_its_sign_and_still_counts(run) -> None:
    # Award W03 of SYNR03: T04 200.00 then T05 -450.00, net -250.00.
    only_w03 = {
        "time_range": FY2025,
        "filters": [{"field": "recipient", "op": "eq", "value": "SYNR03"}],
    }
    assert run({"metric": "obligations", **only_w03}) == [(d("-250.00"),)]
    assert run({"metric": "award_count", **only_w03}) == [(1,)]
    assert run({"metric": "avg_award_value", **only_w03}) == [(pytest.approx(-250.0),)]


def test_recipients_roll_up_to_their_parent(run) -> None:
    rows = dict(run({"metric": "obligations", "dimensions": ["recipient"], "time_range": FY2025}))
    assert rows["SYNP01"] == d("1800.00")  # SYNR01 1500 + SYNR02 300
    assert "SYNR01" not in rows and "SYNR02" not in rows
    assert len(rows) == 12
    assert sum(rows.values()) == d("9050.00")


def test_a_subsidiary_uei_matches_nothing_at_the_reporting_level(run) -> None:
    query = {
        "metric": "obligations",
        "filters": [{"field": "recipient", "op": "eq", "value": "SYNR01"}],
    }
    assert run(query) == [(None,)]


def test_obligations_by_competed_keeps_transactions_with_no_code(run) -> None:
    rows = run({"metric": "obligations", "dimensions": ["competed"], "time_range": FY2025})
    # competed: T01 T02 T04 T05 T07 T08 T10 T13 T14; not: T03 T06 T09 T11 T15; no code: T12
    assert rows == [(False, d("3400.00")), (True, d("5050.00")), (None, d("600.00"))]


def test_time_range_is_inclusive_on_both_ends(run) -> None:
    first = {"start": "2024-10-01", "end": "2024-10-01"}
    last = {"start": "2025-09-30", "end": "2025-09-30"}
    assert run({"metric": "obligations", "time_range": first}) == [(d("1000.00"),)]  # T01
    assert run({"metric": "obligations", "time_range": last}) == [(d("500.00"),)]  # T13


# --- filters -----------------------------------------------------------------------------------


def test_in_filter_with_award_type_grouping(run) -> None:
    rows = run(
        {
            "metric": "obligations",
            "dimensions": ["award_type"],
            "filters": [{"field": "award_type", "op": "in", "value": ["A", "B"]}],
            "time_range": FY2025,
        }
    )
    # A: T06 1200 + T10 800; B: T04 200 - T05 450 + T11 700
    assert rows == [("A", d("2000.00")), ("B", d("450.00"))]


@pytest.mark.parametrize(
    ("op", "value", "expected"),
    [
        ("eq", "VA", "4600.00"),  # T01 T02 T06 T09 T12 T14
        ("ne", "VA", "4450.00"),  # the other nine, abroad T08 and T13 included
        ("not_in", ["VA", "MD"], "2350.00"),  # T04 T05 T08 T10 T13 T15
    ],
)
def test_negative_filters_keep_work_performed_abroad(run, op, value, expected) -> None:
    filters = [{"field": "pop_state", "op": op, "value": value}]
    query = {"metric": "obligations", "filters": filters, "time_range": FY2025}
    assert run(query) == [(d(expected),)]


def test_value_with_a_quote_and_a_semicolon_is_bound_and_matches(run) -> None:
    filters = [{"field": "sub_agency", "op": "eq", "value": "SA'3;"}]
    query = {"metric": "obligations", "filters": filters, "time_range": FY2025}
    assert run(query) == [(d("1300.00"),)]  # T10 800 + T13 500


def test_injection_attempt_is_only_a_value_that_matches_nothing(run, conn) -> None:
    hostile = "x'); DROP TABLE fct_contract_transaction; --"
    filters = [{"field": "sub_agency", "op": "eq", "value": hostile}]
    assert run({"metric": "obligations", "filters": filters}) == [(None,)]
    assert conn.execute("SELECT count(*) FROM fct_contract_transaction").fetchone() == (16,)


# --- award_count: distinct awards active in the period ------------------------------------------


def test_award_count_fy2025(run) -> None:
    # W01..W13; W14 has only T16, in fiscal year 2024
    assert run({"metric": "award_count", "time_range": FY2025}) == [(13,)]


def test_award_count_by_fiscal_month_counts_an_award_in_each_active_month(run) -> None:
    rows = run({"metric": "award_count", "grain": "fiscal_month", "time_range": FY2025})
    # W01 is active in months 1 and 2, W03 in months 3 and 5: 15 month-awards, 13 awards.
    assert [count for _, _, count in rows] == [2, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2]
    assert sum(count for _, _, count in rows) == 15


def test_award_count_by_award_type(run) -> None:
    rows = run({"metric": "award_count", "dimensions": ["award_type"], "time_range": FY2025})
    assert rows == [("A", 2), ("B", 2), ("C", 6), ("D", 3)]


# --- avg_award_value: obligations over award_count --------------------------------------------


def test_avg_award_value_fy2025(run) -> None:
    assert run({"metric": "avg_award_value", "time_range": FY2025}) == [(pytest.approx(9050 / 13),)]


def test_avg_award_value_by_fiscal_quarter(run) -> None:
    rows = run({"metric": "avg_award_value", "grain": "fiscal_quarter", "time_range": FY2025})
    # Q1 2300 / 4 (W01 W02 W03 W13); Q2 1850 / 3 (W03 W04 W05); Q3 2700 / 3; Q4 2200 / 4
    assert rows == [
        (2025, 1, pytest.approx(575.0)),
        (2025, 2, pytest.approx(1850 / 3)),
        (2025, 3, pytest.approx(900.0)),
        (2025, 4, pytest.approx(550.0)),
    ]


# --- supplier_concentration_top10: top ten parents' share -------------------------------------


def test_supplier_concentration_fy2025(run) -> None:
    # Reporting totals: SYNP01 1800, SYNR04 1200, SYNR05 1100, SYNR06 1000, SYNR07 900,
    # SYNR08 800, SYNR09 700, SYNR10 600, SYNR11 500, SYNR12 400 (top ten, 9000), then
    # SYNR13 300 and SYNR03 -250. Share 9000 / 9050. Without the parent roll-up the top ten
    # would hold 8700.
    rows = run({"metric": "supplier_concentration_top10", "time_range": FY2025})
    assert rows == [(pytest.approx(9000 / 9050),)]


def test_supplier_concentration_without_time_range(run) -> None:
    # SYNR13 now holds 5300 and ranks first; SYNR12 (400) drops to eleventh.
    # Top ten 5300 + 1800 + 1200 + 1100 + 1000 + 900 + 800 + 700 + 600 + 500 = 13900.
    rows = run({"metric": "supplier_concentration_top10"})
    assert rows == [(pytest.approx(13900 / 14050),)]


def test_supplier_concentration_is_one_with_ten_or_fewer_recipients(run) -> None:
    rows = run(
        {
            "metric": "supplier_concentration_top10",
            "dimensions": ["sub_agency"],
            "time_range": FY2025,
        }
    )
    assert rows == [("SA'3;", 1.0), ("SA1", 1.0), ("SA2", 1.0)]


def test_supplier_concentration_by_fiscal_year(run) -> None:
    rows = run({"metric": "supplier_concentration_top10", "grain": "fiscal_year"})
    assert rows == [(2024, 1.0), (2025, pytest.approx(9000 / 9050))]


# --- competition_rate: competed share of net obligations --------------------------------------


def test_competition_rate_fy2025(run) -> None:
    # Competed: 1000 + 500 + 200 - 450 + 1100 + 1000 + 800 + 500 + 400 = 5050 of 9050.
    assert run({"metric": "competition_rate", "time_range": FY2025}) == [
        (pytest.approx(5050 / 9050),)
    ]


def test_competition_rate_by_fiscal_quarter(run) -> None:
    rows = run({"metric": "competition_rate", "grain": "fiscal_quarter", "time_range": FY2025})
    # Q1 1700 / 2300; Q2 (1100 - 450) / 1850; Q3 1800 / 2700; Q4 (500 + 400) / 2200
    assert rows == [
        (2025, 1, pytest.approx(1700 / 2300)),
        (2025, 2, pytest.approx(650 / 1850)),
        (2025, 3, pytest.approx(1800 / 2700)),
        (2025, 4, pytest.approx(900 / 2200)),
    ]


def test_competition_rate_counts_a_missing_code_as_not_competed(run) -> None:
    # SYNR10 has one transaction, T12, with no competition code.
    filters = [{"field": "recipient", "op": "eq", "value": "SYNR10"}]
    assert run({"metric": "competition_rate", "filters": filters}) == [(0.0,)]


# --- empty selections ---------------------------------------------------------------------------


def test_a_total_over_an_empty_selection_is_one_row(run) -> None:
    nothing = {"time_range": {"start": "2030-10-01", "end": "2031-09-30"}}
    assert run({"metric": "obligations", **nothing}) == [(None,)]
    assert run({"metric": "award_count", **nothing}) == [(0,)]
    assert run({"metric": "avg_award_value", **nothing}) == [(None,)]
    assert run({"metric": "supplier_concentration_top10", **nothing}) == [(None,)]
    assert run({"metric": "competition_rate", **nothing}) == [(None,)]
    assert run({"metric": "obligations", "grain": "fiscal_month", **nothing}) == []


# --- the fixture itself ----------------------------------------------------------------------


def test_fixture_fiscal_calendar_follows_the_contract(conn) -> None:
    days = [dt.date(2024, 9, 30), dt.date(2024, 10, 1), dt.date(2025, 1, 15), dt.date(2025, 9, 30)]
    rows = conn.execute(
        'SELECT "date", fiscal_year, fiscal_quarter, fiscal_month FROM dim_date '
        'WHERE "date" IN (?, ?, ?, ?) ORDER BY "date"',
        days,
    ).fetchall()
    assert rows == [
        (days[0], 2024, 4, 12),
        (days[1], 2025, 1, 1),
        (days[2], 2025, 2, 4),
        (days[3], 2025, 4, 12),
    ]
