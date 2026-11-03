"""The publisher's golden values, reproduced through the compiler on the S2 warehouse.

Every mapped structured query of ``evals/golden_queries.yaml`` is compiled from the catalog
and run on the read-only query connection; its result must equal the golden value under the
rule of docs/reproduction.md. A value with a known difference stays failing: it is marked
xfail with its reason, strictly, so it turns red the day it starts to match.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
import yaml

from agentic_bi.reproduce import (
    DEFAULT_GOLDEN,
    DEFAULT_MAPPING,
    DIFFERS,
    NOT_EXPRESSIBLE,
    REPRODUCED,
    MappedQuery,
    NotExpressible,
    load_mapping,
    summary,
)

MAPPING = load_mapping()
MAPPED = [entry for entry in MAPPING.entries if isinstance(entry, MappedQuery)]
NOT_EXPRESSIBLE_ENTRIES = [entry for entry in MAPPING.entries if isinstance(entry, NotExpressible)]
KNOWN = [entry for entry in MAPPED if entry.known_difference is not None]
GOLDEN_RAW = {v["id"]: v for v in json.loads(DEFAULT_GOLDEN.read_text("utf-8"))["values"]}
RECIPIENTS = [i for i in GOLDEN_RAW if i.startswith("obligations.recipient.")]


def _mapped_params():
    for entry in MAPPED:
        marks = []
        if entry.known_difference is not None:
            marks.append(pytest.mark.xfail(strict=True, reason=entry.known_difference))
        yield pytest.param(entry.id, id=entry.id, marks=marks)


@pytest.mark.parametrize("value_id", list(_mapped_params()))
def test_golden_value_reproduces(outcomes, value_id: str) -> None:
    outcome = outcomes[value_id]
    assert outcome.status == REPRODUCED, (
        f"{outcome.status}: golden {outcome.golden}, reproduced {outcome.reproduced}, "
        f"difference {outcome.difference}; {outcome.reason or ''}"
    )


def test_no_outcome_is_unexpected(outcomes) -> None:
    # every mapped value reproduces or is a known difference that still differs, and every
    # not-expressible value is still refused as its entry says
    assert summary(tuple(outcomes.values()))["unexpected"] == []


def test_every_golden_id_appears_exactly_once_in_the_mapping_file() -> None:
    raw = yaml.safe_load(DEFAULT_MAPPING.read_text("utf-8"))
    ids = [entry["id"] for entry in raw["values"]]
    assert len(ids) == len(set(ids)) == len(GOLDEN_RAW) == 48
    assert set(ids) == set(GOLDEN_RAW)


def test_reproduction_reaches_the_brief_floor(outcomes) -> None:
    reproduced = {i for i, o in outcomes.items() if o.status == REPRODUCED}
    assert len(reproduced) >= 30
    assert sum(outcomes[i].tag == "derived" for i in reproduced) >= 5
    months = {f"obligations.fiscal_month.{m:02d}" for m in range(1, 13)}
    assert months <= reproduced
    assert "recipients.top10_reporting_share" in reproduced
    assert "obligations.extent_competed.unreported" in reproduced


def test_every_recipient_is_mapped_and_each_one_that_differs_is_a_known_difference(
    outcomes,
) -> None:
    assert len(RECIPIENTS) == 5
    for value_id in RECIPIENTS:
        outcome = outcomes[value_id]
        assert outcome.status != NOT_EXPRESSIBLE, value_id
        assert (outcome.status == REPRODUCED) == (outcome.known_difference is None), value_id


@pytest.mark.parametrize("entry", KNOWN, ids=[entry.id for entry in KNOWN])
def test_known_difference_still_differs_with_both_numbers(outcomes, entry: MappedQuery) -> None:
    outcome = outcomes[entry.id]
    # differs, not error: the query ran and gave a number that is not the golden one
    assert outcome.status == DIFFERS
    assert outcome.reproduced is not None and outcome.difference is not None
    assert outcome.reproduced - Decimal(outcome.golden) == outcome.difference != 0


@pytest.mark.parametrize(
    "entry", NOT_EXPRESSIBLE_ENTRIES, ids=[entry.id for entry in NOT_EXPRESSIBLE_ENTRIES]
)
def test_not_expressible_value_is_refused_by_the_compiler(outcomes, entry: NotExpressible) -> None:
    outcome = outcomes[entry.id]
    assert outcome.status == NOT_EXPRESSIBLE
    assert outcome.reproduced is None and outcome.difference is None
    assert entry.attempt is not None, "give the nearest query and its refusal"
    assert outcome.attempt["refusal"] == entry.attempt.refusal


def test_every_mapped_outcome_carries_its_query_sql_and_bound_parameters(outcomes) -> None:
    for entry in MAPPED:
        outcome = outcomes[entry.id]
        assert outcome.query == entry.query.to_dict()
        assert outcome.sql and outcome.sql.lstrip().upper().startswith(("SELECT", "WITH"))
        # values are bound, never written into the SQL text (ADR-0002 point 4)
        assert "'" not in outcome.sql and ";" not in outcome.sql
        assert outcome.sql.count("?") == len(outcome.params)
        for flt in entry.query.filters:
            assert flt.value in outcome.params


def test_reproduced_types_follow_the_metric_kind(outcomes) -> None:
    kinds = {
        "obligations.total": Decimal,
        "awards.active_in_period.count": int,
        "awards.average_net_obligation": Decimal,
        "recipients.top10_reporting_share": Decimal,
    }
    rules = {
        "obligations.total": "exact",
        "awards.active_in_period.count": "exact",
        "awards.average_net_obligation": "half_unit_of_last_place",
        "recipients.top10_reporting_share": "half_unit_of_last_place",
    }
    for value_id, kind in kinds.items():
        assert type(outcomes[value_id].reproduced) is kind, value_id
        assert outcomes[value_id].rule == rules[value_id], value_id


def test_ratios_reproduce_within_half_a_unit_but_not_to_more_places(outcomes) -> None:
    # the golden ratios are rounded: the unrounded value differs, inside the tolerance
    share = outcomes["recipients.top10_reporting_share"]
    assert share.tolerance == Decimal("0.0000005")
    assert 0 < abs(share.difference) <= share.tolerance
    average = outcomes["awards.average_net_obligation"]
    assert average.tolerance == Decimal("0.005")
    assert 0 < abs(average.difference) <= average.tolerance


def test_sub_agency_code_of_the_mapping_names_the_golden_sub_agency(plain_connection) -> None:
    entry = MAPPING.by_id()["obligations.sub_agency.environmental_protection_agency"]
    (code,) = [flt.value for flt in entry.query.filters if flt.field == "sub_agency"]
    name = GOLDEN_RAW[entry.id]["dimensions"]["sub_agency_name"]
    rows = plain_connection.execute(
        "select sub_agency_name from dim_sub_agency where sub_agency_code = ?", [code]
    ).fetchall()
    assert rows == [(name,)]


@pytest.mark.parametrize("value_id", RECIPIENTS)
def test_recipient_reproduces_exactly_when_its_reporting_uei_selects_its_own_transactions(
    plain_connection, outcomes, value_id: str
) -> None:
    """The catalog's recipient is the reporting (parent) UEI; the publisher's is the own UEI.

    The two coincide for a recipient that is its own parent on every transaction and has no
    subsidiary; then the reporting-level query must reproduce the publisher's value. When
    they do not coincide the value is a known difference, and the publisher's figure is
    still the plain sum over the recipient's own transactions.
    """
    uei = GOLDEN_RAW[value_id]["dimensions"]["recipient_uei"]
    own = set(
        plain_connection.execute(
            "select transaction_key from fct_contract_transaction where recipient_uei = ?", [uei]
        ).fetchall()
    )
    reporting = set(
        plain_connection.execute(
            "select transaction_key from fct_contract_transaction where reporting_uei = ?", [uei]
        ).fetchall()
    )
    coincide = own == reporting
    assert (outcomes[value_id].status == REPRODUCED) == coincide
    (own_sum,) = plain_connection.execute(
        "select sum(obligation_amount) from fct_contract_transaction where recipient_uei = ?",
        [uei],
    ).fetchone()
    assert own_sum == Decimal(GOLDEN_RAW[value_id]["value"])
