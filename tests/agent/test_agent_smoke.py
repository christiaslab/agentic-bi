"""The fixtures build, and the tool box opens on both databases."""

from decimal import Decimal


def test_synthetic_fixture_holds_the_documented_totals(synthetic_toolbox) -> None:
    outcome = synthetic_toolbox.call("run_query", {"metric": "obligations"})
    assert not outcome.is_error
    assert outcome.run.rows == ((Decimal("3600.00"),),)


def test_warehouse_holds_the_snapshot_total(warehouse_toolbox) -> None:
    outcome = warehouse_toolbox.call("run_query", {"metric": "obligations"})
    assert outcome.run.rows == ((Decimal("1697318332.65"),),)
