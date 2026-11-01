"""The golden capture on the recorded responses and the trimmed snapshot."""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from usaspending_replay import CAPTURED_AT, FIXTURES, TRIMMED_CSV, Replay

from agentic_bi.ingest import fetch, golden
from agentic_bi.ingest.client import Client

KEYS = {"id", "tag", "description", "value", "unit", "dimensions", "endpoint", "method"}
KEYS |= {"query", "request_body", "capture_date"}


def _client(replay: Replay) -> Client:
    return Client(replay, attempts=2, sleep=lambda _: None)


@pytest.fixture
def snapshot_dir(tmp_path: Path) -> Path:
    out = tmp_path / "snapshot"
    fetch.run(out, _client(Replay()), now=lambda: CAPTURED_AT, sleep=lambda _: None)
    return out


def _run(tmp_path: Path, snapshot_dir: Path, when=CAPTURED_AT) -> dict:
    return golden.run(tmp_path / "golden", snapshot_dir, _client(Replay()), now=lambda: when)


def _recorded(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_writes_30_to_50_tagged_values(tmp_path: Path, snapshot_dir: Path) -> None:
    result = _run(tmp_path, snapshot_dir)
    on_disk = json.loads((tmp_path / "golden" / golden.GOLDEN_NAME).read_text(encoding="utf-8"))
    assert on_disk == result
    values = result["values"]
    assert 30 <= len(values) <= 50
    assert len({v["id"] for v in values}) == len(values)
    for value in values:
        assert KEYS <= set(value), value["id"]
        assert value["tag"] in {"owner_api", "derived"}
        assert value["capture_date"] == result["capture_date_utc"] == "2026-11-01"
        assert value["endpoint"].startswith("https://api.usaspending.gov/api/v2/")
        assert value["request_body"] is not None or value["query"] is not None
        if value["tag"] == "derived":
            assert value["derivation"]["sql"].startswith(("select", "with"))
        else:
            assert value["response_path"]
    assert {v["tag"] for v in values} == {"owner_api", "derived"}
    assert result["snapshot"]["capture_date_utc"] == "2026-11-01"


def test_owner_values_are_the_recorded_figures(tmp_path: Path, snapshot_dir: Path) -> None:
    values = {v["id"]: v for v in _run(tmp_path, snapshot_dir)["values"]}
    count = _recorded("spending_by_transaction_count.json")["results"]["contracts"]
    assert values["transactions.count"]["value"] == count == 12687
    assert values["obligations.total"]["value"] == "1697318332.65"
    months = _recorded("spending_over_time_month.json")["results"]
    october = next(m for m in months if m["time_period"]["month"] == "1")
    assert values["obligations.fiscal_month.01"]["value"] == f"{october['aggregated_amount']:.2f}"
    top = _recorded("spending_by_category_recipient.json")["results"][0]
    assert values[f"obligations.recipient.{top['uei']}"]["value"] == f"{top['amount']:.2f}"
    states = [v for v in values.values() if v["id"].startswith("obligations.pop_state.")]
    amounts = [Decimal(v["value"]) for v in states]
    assert len(states) == golden.TOP_N and amounts == sorted(amounts, reverse=True)


def test_derived_values_match_an_independent_computation(
    tmp_path: Path, snapshot_dir: Path
) -> None:
    values = {v["id"]: v for v in _run(tmp_path, snapshot_dir)["values"]}
    rows = list(csv.DictReader(io.StringIO(TRIMMED_CSV.read_text(encoding="utf-8"))))
    net = defaultdict(Decimal)
    reporting = defaultdict(Decimal)
    competed = defaultdict(Decimal)
    for row in rows:
        amount = Decimal(row["federal_action_obligation"])
        net[row["contract_award_unique_key"]] += amount
        reporting[row["recipient_parent_uei"] or row["recipient_uei"]] += amount
        competed[row["extent_competed_code"] or "unreported"] += amount
    total = sum(net.values())
    assert values["awards.active_in_period.count"]["value"] == len(net)
    assert values["awards.net_negative.count"]["value"] == sum(v < 0 for v in net.values())
    deobligations = sum(Decimal(r["federal_action_obligation"]) for r in rows) - sum(
        max(Decimal(r["federal_action_obligation"]), Decimal(0)) for r in rows
    )
    assert values["obligations.deobligations.sum"]["value"] == f"{deobligations:.2f}"
    average = (total / len(net)).quantize(Decimal("0.01"))
    assert values["awards.average_net_obligation"]["value"] == f"{average}"
    top10 = sum(sorted(reporting.values(), reverse=True)[:10])
    share = (top10 / total).quantize(Decimal("0.000001"))
    assert values["recipients.top10_reporting_share"]["value"] == f"{share}"
    lowest = min(net, key=lambda key: (net[key], key))
    assert values["award.most_negative.net_obligation"]["dimensions"] == {"award_key": lowest}
    assert values["award.most_negative.net_obligation"]["value"] == f"{net[lowest]:.2f}"
    for code, amount in competed.items():
        assert values[f"obligations.extent_competed.{code}"]["value"] == f"{amount:.2f}"


def test_trimmed_snapshot_is_reported_as_a_mismatch(tmp_path: Path, snapshot_dir: Path) -> None:
    reconciliation = _run(tmp_path, snapshot_dir)["reconciliation"]
    assert reconciliation["status"] == "mismatch"
    rows = [c for c in reconciliation["checks"] if c["measure"] == "row_count"]
    assert [(c["snapshot"], c["publisher"], c["difference"]) for c in rows] == [
        ("30", "12687", "-12657"),
        ("30", "12687", "-12657"),
    ]
    sums = [c for c in reconciliation["checks"] if c["measure"] == "obligation_sum"]
    assert len(sums) == 3 and {c["publisher"] for c in sums} == {"1697318332.65"}
    assert not any(c["match"] for c in reconciliation["checks"])


def test_reconcile_to_the_cent() -> None:
    publisher = [
        {"measure": "row_count", "value": 3, "source": {}},
        {"measure": "obligation_sum", "value": Decimal("10.00"), "source": {}},
    ]
    same = golden.reconcile({"row_count": 3, "obligation_sum": Decimal("10.00")}, publisher)
    assert same["status"] == "match"
    off = golden.reconcile({"row_count": 3, "obligation_sum": Decimal("10.01")}, publisher)
    assert off["status"] == "mismatch"
    assert off["checks"][1]["difference"] == "0.01"


def test_snapshot_of_another_day_is_refused(tmp_path: Path, snapshot_dir: Path) -> None:
    with pytest.raises(golden.GoldenError, match="never mixes days"):
        _run(tmp_path, snapshot_dir, when=CAPTURED_AT + timedelta(days=1))
    assert not (tmp_path / "golden").exists()


def test_snapshot_with_a_changed_file_is_refused(tmp_path: Path, snapshot_dir: Path) -> None:
    parquet = snapshot_dir / fetch.PARQUET_NAME
    parquet.write_bytes(parquet.read_bytes() + b"\0")
    with pytest.raises(golden.GoldenError, match="sha256"):
        _run(tmp_path, snapshot_dir)


def test_manifest_naming_a_file_outside_the_snapshot_is_refused(
    tmp_path: Path, snapshot_dir: Path
) -> None:
    path = snapshot_dir / fetch.MANIFEST_NAME
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "../elsewhere.parquet"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(golden.GoldenError, match="outside the snapshot directory"):
        _run(tmp_path, snapshot_dir)
