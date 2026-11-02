"""Capture the ADR-0001 slice as a column-pruned Parquet snapshot with a manifest.

Usage: ``uv run python -m agentic_bi.ingest.fetch --out data/snapshot``

The script asks the transaction download for exactly the allow-listed columns, waits
for the file, checks the header against the pinned field list (ADR-0001 point 7: a
missing or renamed field stops the run), checks every row against the slice, and
writes ``contract_transactions.parquet`` and ``manifest.json``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import time
import urllib.parse
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import duckdb

from agentic_bi.ingest import spec
from agentic_bi.ingest.client import Client, HttpError

DOWNLOAD_PATH = "/api/v2/download/transactions/"
STATUS_PATH = "/api/v2/download/status"
PARQUET_NAME = "contract_transactions.parquet"
MANIFEST_NAME = "manifest.json"
SIZE_LIMIT_BYTES = 10 * 1024 * 1024
MAX_MEMBER_BYTES = 1024 * 1024 * 1024
PRIME_MEMBER = re.compile(r"^Contracts_PrimeTransactions_[A-Za-z0-9_-]+\.csv$")
GENERATED_FILE = re.compile(r"^/generated_downloads/[A-Za-z0-9_.-]+\.zip$")


class SnapshotError(RuntimeError):
    """The download does not match the pinned fields or the slice; nothing is written."""


def download_request() -> dict:
    """The request body sent to the transaction download, recorded in the manifest."""
    return {"filters": spec.filters(), "columns": list(spec.FIELDS), "file_format": "csv"}


def request_download(
    client: Client,
    *,
    max_wait: float = 1800.0,
    poll_every: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict, dict, bytes]:
    """Start the download, wait until it is finished, and return (response, status, zip)."""
    response = client.post_json(DOWNLOAD_PATH, download_request())
    file_name = response.get("file_name")
    if not isinstance(file_name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+\.zip", file_name):
        raise SnapshotError("the download response carries no usable file name")
    waited = 0.0
    while True:
        status = client.get_json(STATUS_PATH, {"file_name": file_name})
        state = status.get("status")
        if state == "finished":
            break
        if state == "failed":
            raise SnapshotError(f"the download failed at the source: {status.get('message')}")
        if waited >= max_wait:
            raise SnapshotError(f"the download was not ready after {max_wait:.0f} s")
        sleep(poll_every)
        waited += poll_every
    file_url = str(status.get("file_url") or response.get("file_url") or "")
    parts = urllib.parse.urlsplit(file_url)
    if parts.hostname != spec.FILES_HOST or not GENERATED_FILE.match(parts.path):
        raise HttpError(f"refused download location on host {parts.hostname}")
    return response, status, client.get_bytes(file_url)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def check_header(header: list[str]) -> None:
    """Fail loudly unless the header is exactly the pinned allow-list."""
    excluded = spec.excluded_in(header)
    if excluded:
        raise SnapshotError(f"excluded columns in the download: {', '.join(excluded)}")
    if tuple(header) != spec.FIELDS:
        missing = [name for name in spec.FIELDS if name not in header]
        unexpected = [name for name in header if name not in spec.FIELDS]
        raise SnapshotError(
            "the download header does not match the pinned field list "
            f"(missing: {missing or 'none'}; unexpected: {unexpected or 'none'}; "
            "same names in another order count as drift too)"
        )


def prime_members(archive: zipfile.ZipFile) -> list[str]:
    """The prime-transaction CSV members, in the order the source numbers them."""
    names = [name for name in archive.namelist() if PRIME_MEMBER.match(name)]
    if not names:
        raise SnapshotError("the download holds no Contracts_PrimeTransactions file")
    return sorted(names, key=lambda name: int(re.search(r"_(\d+)\.csv$", name).group(1)))


def extract_rows(zip_bytes: bytes, target: Path) -> tuple[list[dict], int]:
    """Copy the prime-transaction rows into one CSV at ``target``; never touch other members.

    Returns the member records (name, sha256, rows) and the total row count.
    """
    members: list[dict] = []
    total = 0
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        names = prime_members(archive)
        with target.open("w", encoding="utf-8", newline="") as out:
            writer = csv.writer(out, lineterminator="\n")
            writer.writerow(spec.FIELDS)
            for name in names:
                if archive.getinfo(name).file_size > MAX_MEMBER_BYTES:
                    raise SnapshotError(f"{name} is larger than {MAX_MEMBER_BYTES} bytes")
                raw = archive.read(name)
                reader = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
                header = next(reader, None)
                if header is None:
                    raise SnapshotError(f"{name} is empty")
                check_header(header)
                count = 0
                for row in reader:
                    if len(row) != len(spec.FIELDS):
                        raise SnapshotError(f"{name}: a row has {len(row)} fields")
                    writer.writerow(row)
                    count += 1
                members.append({"name": name, "sha256": sha256_bytes(raw), "rows": count})
                total += count
    return members, total


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def typed_select() -> str:
    """The cast list from the raw VARCHAR table to the snapshot types."""
    columns = []
    for name in spec.FIELDS:
        if name in spec.DECIMAL_FIELDS:
            columns.append(f"cast({_quote(name)} as decimal(18,2)) as {_quote(name)}")
        elif name in spec.DATE_FIELDS:
            columns.append(f"cast({_quote(name)} as date) as {_quote(name)}")
        elif name in spec.INTEGER_FIELDS:
            columns.append(f"cast({_quote(name)} as integer) as {_quote(name)}")
        else:
            columns.append(_quote(name))
    return "select " + ", ".join(columns) + " from raw order by contract_transaction_unique_key"


def check_slice(con: duckdb.DuckDBPyConnection, expected_rows: int) -> tuple[int, Decimal]:
    """Every row inside the slice, keys unique; return (row count, obligation sum)."""
    rows, keys, distinct_keys = con.execute(
        "select count(*), count(contract_transaction_unique_key), "
        "count(distinct contract_transaction_unique_key) from snapshot"
    ).fetchone()
    if rows != expected_rows:
        raise SnapshotError(f"DuckDB read {rows} rows, the CSV holds {expected_rows}")
    if keys != rows or distinct_keys != rows:
        raise SnapshotError("contract_transaction_unique_key is missing or repeated")
    checks = {
        "awarding agency code other than the slice": (
            "select count(*) from snapshot where awarding_agency_code is distinct from ?",
            [spec.AGENCY_TOPTIER_CODE],
        ),
        "award type outside A, B, C, D": (
            "select count(*) from snapshot where award_type_code is null "
            "or not list_contains(?, award_type_code)",
            [list(spec.AWARD_TYPE_CODES)],
        ),
        "action date outside the fiscal year": (
            "select count(*) from snapshot where action_date is null "
            "or action_date < cast(? as date) or action_date > cast(? as date)",
            [spec.START_DATE, spec.END_DATE],
        ),
        "missing obligation": (
            "select count(*) from snapshot where federal_action_obligation is null",
            [],
        ),
    }
    for label, (sql, params) in checks.items():
        (bad,) = con.execute(sql, params).fetchone()
        if bad:
            raise SnapshotError(f"{bad} rows with {label}")
    (total,) = con.execute("select sum(federal_action_obligation) from snapshot").fetchone()
    return rows, Decimal(total)


def build_snapshot(zip_bytes: bytes, out_dir: Path) -> dict:
    """Write the Parquet into ``out_dir`` and return the facts the manifest records."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="agentic-bi-fetch-") as scratch:
        csv_path = Path(scratch) / "prime_transactions.csv"
        members, csv_rows = extract_rows(zip_bytes, csv_path)
        con = duckdb.connect()
        try:
            con.execute("set threads = 1")
            con.execute(
                "create table raw as select * from read_csv(?, header = true, "
                "all_varchar = true, delim = ',', quote = '\"', escape = '\"', "
                "strict_mode = true)",
                [str(csv_path)],
            )
            read_names = [row[0] for row in con.execute("describe raw").fetchall()]
            check_header(read_names)
            con.execute("create table snapshot as " + typed_select())
            rows, total = check_slice(con, csv_rows)
            partial = out_dir / (PARQUET_NAME + ".partial")
            con.table("snapshot").write_parquet(str(partial), compression="zstd")
        finally:
            con.close()
    final = out_dir / PARQUET_NAME
    os.replace(partial, final)
    return {"members": members, "row_count": rows, "obligation_sum": total, "path": final}


def write_manifest(
    out_dir: Path, *, captured_at: datetime, response: dict, status: dict, built: dict
) -> dict:
    """Write ``manifest.json`` next to the Parquet and return it."""
    path: Path = built["path"]
    manifest = {
        "schema_version": 1,
        "dataset": (
            "USAspending.gov prime contract transactions: awarding agency EPA (toptier 068), "
            "award types A, B, C, D, action dates 2024-10-01 to 2025-09-30 (FY2025)"
        ),
        "capture_date_utc": captured_at.date().isoformat(),
        "captured_at_utc": captured_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "source": {
            "endpoint": spec.API_BASE + DOWNLOAD_PATH,
            "request_body": download_request(),
            "file_name": response.get("file_name"),
            "status_total_rows": status.get("total_rows"),
            "prime_transaction_members": built["members"],
        },
        "filters": spec.filters(),
        "fields": list(spec.FIELDS),
        "row_count": built["row_count"],
        "obligation_sum": f"{built['obligation_sum']:.2f}",
        "files": [{"path": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}],
    }
    partial = out_dir / (MANIFEST_NAME + ".partial")
    partial.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, out_dir / MANIFEST_NAME)
    return manifest


def run(
    out_dir: Path,
    client: Client,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    max_wait: float = 1800.0,
    poll_every: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Fetch, check and write the snapshot; return the manifest."""
    captured_at = now()
    response, status, zip_bytes = request_download(
        client, max_wait=max_wait, poll_every=poll_every, sleep=sleep
    )
    built = build_snapshot(zip_bytes, out_dir)
    return write_manifest(
        out_dir, captured_at=captured_at, response=response, status=status, built=built
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="directory for the snapshot")
    parser.add_argument("--max-wait", type=float, default=1800.0, help="seconds to wait")
    args = parser.parse_args(argv)
    try:
        manifest = run(args.out, Client(), max_wait=args.max_wait)
    except (SnapshotError, HttpError) as error:
        print(f"fetch stopped: {error}", file=sys.stderr)
        return 1
    size = manifest["files"][0]["bytes"]
    print(f"capture date (UTC): {manifest['capture_date_utc']}")
    print(f"rows: {manifest['row_count']}")
    print(f"sum of federal_action_obligation: {manifest['obligation_sum']}")
    print(f"{manifest['files'][0]['path']}: {size} bytes, sha256 {manifest['files'][0]['sha256']}")
    if size > SIZE_LIMIT_BYTES:
        print("the Parquet is over 10 MB: commit the manifest only (ADR-0001)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
