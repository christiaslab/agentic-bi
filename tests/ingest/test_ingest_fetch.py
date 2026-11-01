"""The fetch on the recorded, trimmed download: snapshot, manifest and the loud failures."""

from __future__ import annotations

import csv
import io
import json
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from usaspending_replay import CAPTURED_AT, TRIMMED_CSV, Replay, trimmed_zip, url_of

from agentic_bi.ingest import fetch, spec
from agentic_bi.ingest.client import Client, HttpError

STATUS_URL = url_of("download_status_finished.json")
DOWNLOAD_URL = url_of("download_transactions.json")


def _run(tmp_path: Path, replay: Replay) -> dict:
    client = Client(replay, attempts=2, sleep=lambda _: None)
    return fetch.run(tmp_path, client, now=lambda: CAPTURED_AT, sleep=lambda _: None)


def _csv_rows() -> list[dict]:
    return list(csv.DictReader(io.StringIO(TRIMMED_CSV.read_text(encoding="utf-8"))))


def _schema(path: Path) -> list[tuple[str, str]]:
    con = duckdb.connect()
    rows = con.execute("describe select * from read_parquet(?)", [str(path)]).fetchall()
    return [(row[0], row[1]) for row in rows]


def test_writes_parquet_and_manifest(tmp_path: Path, replay: Replay) -> None:
    manifest = _run(tmp_path, replay)
    rows = _csv_rows()
    parquet = tmp_path / fetch.PARQUET_NAME
    on_disk = json.loads((tmp_path / fetch.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert on_disk == manifest
    assert manifest["capture_date_utc"] == "2026-11-01"
    assert manifest["filters"] == spec.filters()
    assert manifest["fields"] == list(spec.FIELDS)
    assert manifest["source"]["request_body"]["columns"] == list(spec.FIELDS)
    assert manifest["row_count"] == len(rows) == 30
    total = sum(Decimal(row["federal_action_obligation"]) for row in rows)
    assert manifest["obligation_sum"] == f"{total:.2f}"
    assert manifest["files"] == [
        {
            "path": fetch.PARQUET_NAME,
            "bytes": parquet.stat().st_size,
            "sha256": fetch.sha256_file(parquet),
        }
    ]
    # the request asked for the allow-list only
    method, url, body = replay.calls[0]
    assert (method, url) == ("POST", DOWNLOAD_URL)
    assert body == fetch.download_request()


def test_snapshot_schema_is_the_allow_list_with_types(tmp_path: Path, replay: Replay) -> None:
    _run(tmp_path, replay)
    schema = dict(_schema(tmp_path / fetch.PARQUET_NAME))
    assert list(schema) == list(spec.FIELDS)
    assert spec.excluded_in(list(schema)) == []
    assert schema["federal_action_obligation"] == "DECIMAL(18,2)"
    assert schema["action_date"] == "DATE"
    assert schema["action_date_fiscal_year"] == "INTEGER"
    assert schema["awarding_agency_code"] == "VARCHAR"


def test_rerun_writes_the_same_bytes(tmp_path: Path) -> None:
    first = _run(tmp_path / "a", Replay())
    second = _run(tmp_path / "b", Replay())
    assert first["files"] == second["files"]


def test_polls_until_the_download_is_finished(tmp_path: Path, replay: Replay) -> None:
    finished = replay.routes[("GET", STATUS_URL)][0][1]
    running = json.dumps({**json.loads(finished), "status": "running"}).encode()
    replay.set("GET", STATUS_URL, (200, running), (200, running), (200, finished))
    sleeps: list[float] = []
    client = Client(replay, attempts=2, sleep=lambda _: None)
    fetch.run(tmp_path, client, now=lambda: CAPTURED_AT, poll_every=3.0, sleep=sleeps.append)
    assert sleeps == [3.0, 3.0]


def _header_swapped(old: str, new: str) -> str:
    text = TRIMMED_CSV.read_text(encoding="utf-8")
    header, rest = text.split("\n", 1)
    assert old in header.split(",")
    return ",".join(new if name == old else name for name in header.split(",")) + "\n" + rest


def test_renamed_field_fails_loudly_and_writes_nothing(tmp_path: Path) -> None:
    replay = Replay(trimmed_zip(_header_swapped("naics_code", "transaction_naics_code")))
    with pytest.raises(fetch.SnapshotError, match="missing: \\['naics_code'\\]"):
        _run(tmp_path, replay)
    assert not (tmp_path / fetch.PARQUET_NAME).exists()
    assert not (tmp_path / fetch.MANIFEST_NAME).exists()


@pytest.mark.parametrize(
    "excluded",
    ["recipient_phone_number", "recipient_address_line_1", "highly_compensated_officer_1_name"],
)
def test_excluded_column_in_the_download_fails(tmp_path: Path, excluded: str) -> None:
    replay = Replay(trimmed_zip(_header_swapped("award_type", excluded)))
    with pytest.raises(fetch.SnapshotError, match="excluded columns"):
        _run(tmp_path, replay)
    assert not (tmp_path / fetch.PARQUET_NAME).exists()


def test_row_outside_the_slice_fails(tmp_path: Path) -> None:
    text = TRIMMED_CSV.read_text(encoding="utf-8").replace(",2025,068,6800,", ",2025,069,6800,", 1)
    with pytest.raises(fetch.SnapshotError, match="awarding agency code"):
        _run(tmp_path, Replay(trimmed_zip(text)))


def test_download_without_prime_file_fails(tmp_path: Path) -> None:
    replay = Replay(trimmed_zip(member="Contracts_Subawards_only.csv"))
    with pytest.raises(fetch.SnapshotError, match="no Contracts_PrimeTransactions"):
        _run(tmp_path, replay)


def test_failed_download_stops(tmp_path: Path, replay: Replay) -> None:
    finished = json.loads(replay.routes[("GET", STATUS_URL)][0][1])
    replay.set("GET", STATUS_URL, (200, json.dumps({**finished, "status": "failed"}).encode()))
    with pytest.raises(fetch.SnapshotError, match="failed at the source"):
        _run(tmp_path, replay)


def test_download_location_off_the_files_host_is_refused(tmp_path: Path, replay: Replay) -> None:
    finished = json.loads(replay.routes[("GET", STATUS_URL)][0][1])
    moved = {**finished, "file_url": "https://example.com/generated_downloads/x.zip"}
    replay.set("GET", STATUS_URL, (200, json.dumps(moved).encode()))
    with pytest.raises(HttpError, match="refused download location"):
        _run(tmp_path, replay)
