"""Capture the golden values on the snapshot's day, and reconcile the snapshot to them.

Usage: ``uv run python -m agentic_bi.ingest.golden --out docs/golden [--snapshot data/snapshot]``

Every ``owner_api`` value is read from a publisher aggregate endpoint with the slice
filter of ``spec``. Every ``derived`` value is computed from the snapshot by the SQL
recorded next to it. The run refuses a snapshot captured on another UTC day (ADR-0001
point 5) or one whose sha256 differs from its manifest. It writes ``golden.json``; when
the snapshot does not reconcile to the publisher's totals to the cent, the file records
both numbers and the difference, and the run exits with status 1.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import duckdb

from agentic_bi.ingest import spec
from agentic_bi.ingest.client import Client, HttpError
from agentic_bi.ingest.fetch import MANIFEST_NAME, sha256_file

GOLDEN_NAME = "golden.json"
MIN_VALUES = 30
MAX_VALUES = 50
TOP_N = 5
CENT = Decimal("0.01")
RATIO_PLACES = Decimal("0.000001")


class GoldenError(RuntimeError):
    """A rule of the golden capture cannot be met; nothing is written."""


@dataclass(frozen=True)
class Call:
    """One request to a publisher endpoint."""

    key: str
    method: str
    path: str
    params: dict[str, str] | None = None
    body: dict | None = None

    def send(self, client: Client) -> dict:
        if self.method == "GET":
            return client.get_json(self.path, self.params)
        return client.post_json(self.path, self.body or {})

    def source(self) -> dict:
        return {
            "endpoint": spec.API_BASE + self.path,
            "method": self.method,
            "query": self.params,
            "request_body": self.body,
        }


def calls() -> list[Call]:
    """The publisher requests, all on the slice of ``spec``."""
    fy = str(spec.FISCAL_YEAR)
    agency = f"/api/v2/agency/{spec.AGENCY_TOPTIER_CODE}"
    top = {"limit": TOP_N, "page": 1}
    return [
        Call(
            "transaction_count",
            "POST",
            "/api/v2/search/spending_by_transaction_count/",
            body={"filters": spec.filters()},
        ),
        Call(
            "obligations_by_award_category",
            "GET",
            f"{agency}/obligations_by_award_category/",
            params={"fiscal_year": fy},
        ),
        Call(
            "agency_awards",
            "GET",
            f"{agency}/awards/",
            params={"fiscal_year": fy, "award_type_codes": ",".join(spec.AWARD_TYPE_CODES)},
        ),
        Call(
            "awarding_agency",
            "POST",
            "/api/v2/search/spending_by_category/awarding_agency/",
            body={"filters": spec.filters(), "limit": 10, "page": 1},
        ),
        Call(
            "fiscal_month",
            "POST",
            "/api/v2/search/spending_over_time/",
            body={"group": "month", "filters": spec.filters()},
        ),
        Call(
            "recipient",
            "POST",
            "/api/v2/search/spending_by_category/recipient/",
            body={"filters": spec.filters(), **top},
        ),
        Call(
            "psc",
            "POST",
            "/api/v2/search/spending_by_category/psc/",
            body={"filters": spec.filters(), **top},
        ),
        Call(
            "naics",
            "POST",
            "/api/v2/search/spending_by_category/naics/",
            body={"filters": spec.filters(), **top},
        ),
        Call(
            "awarding_subagency",
            "POST",
            "/api/v2/search/spending_by_category/awarding_subagency/",
            body={"filters": spec.filters(), "limit": 10, "page": 1},
        ),
        Call(
            "pop_state",
            "POST",
            "/api/v2/search/spending_by_geography/",
            body={"scope": "place_of_performance", "geo_layer": "state", "filters": spec.filters()},
        ),
    ]


def money(value: object) -> Decimal:
    """An exact amount in cents from a JSON number or string."""
    if value is None or isinstance(value, bool):
        raise GoldenError("an amount is missing in a publisher response")
    return Decimal(str(value)).quantize(CENT)


def count(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise GoldenError(f"a count is not an integer: {value!r}")
    return value


def _value(
    vid: str,
    tag: str,
    description: str,
    value: Decimal | int | str,
    unit: str,
    source: dict,
    capture_date: str,
    *,
    response_path: str | None = None,
    dimensions: dict | None = None,
    derivation: dict | None = None,
) -> dict:
    record = {
        "id": vid,
        "tag": tag,
        "description": description,
        "value": f"{value}" if isinstance(value, Decimal) else value,
        "unit": unit,
        "dimensions": dimensions or {},
        **source,
        "capture_date": capture_date,
    }
    if response_path is not None:
        record["response_path"] = response_path
    if derivation is not None:
        record["derivation"] = derivation
    return record


def _results(response: dict, key: str) -> list[dict]:
    results = response.get("results")
    if not isinstance(results, list):
        raise GoldenError(f"the {key} response has no results list")
    return results


def owner_values(responses: dict[str, dict], by_key: dict[str, Call], day: str) -> list[dict]:
    """The values read from publisher endpoints, tagged ``owner_api``."""
    values: list[dict] = []

    def add(key: str, vid: str, description: str, value, unit: str, path: str, dims=None):
        values.append(
            _value(
                vid,
                "owner_api",
                description,
                value,
                unit,
                by_key[key].source(),
                day,
                response_path=path,
                dimensions=dims,
            )
        )

    tx = responses["transaction_count"].get("results", {}).get("contracts")
    add(
        "transaction_count",
        "transactions.count",
        "Contract transactions (award types A, B, C, D) in the slice",
        count(tx),
        "transactions",
        "results.contracts",
    )
    categories = {
        row.get("category"): row
        for row in _results(responses["obligations_by_award_category"], "award category")
    }
    if "contracts" not in categories:
        raise GoldenError("obligations_by_award_category has no contracts category")
    add(
        "obligations_by_award_category",
        "obligations.total",
        "Net contract obligations (award types A, B, C, D), EPA, FY2025",
        money(categories["contracts"].get("aggregated_amount")),
        "USD",
        "results[category=contracts].aggregated_amount",
    )

    months = {}
    for row in _results(responses["fiscal_month"], "spending_over_time"):
        period = row.get("time_period", {})
        if str(period.get("fiscal_year")) == str(spec.FISCAL_YEAR):
            months[int(period["month"])] = row
    if sorted(months) != list(range(1, 13)):
        raise GoldenError(f"spending_over_time returned fiscal months {sorted(months)}")
    for month in range(1, 13):
        add(
            "fiscal_month",
            f"obligations.fiscal_month.{month:02d}",
            f"Net contract obligations in fiscal month {month} of FY{spec.FISCAL_YEAR} "
            "(month 1 is October)",
            money(months[month].get("aggregated_amount")),
            "USD",
            f"results[time_period.fiscal_year={spec.FISCAL_YEAR},time_period.month={month}]"
            ".aggregated_amount",
            {"fiscal_year": spec.FISCAL_YEAR, "fiscal_month": month},
        )

    ranked = {
        "recipient": ("recipient", "recipient_uei", "uei", "recipient"),
        "psc": ("psc", "psc_code", "code", "product or service code"),
        "naics": ("naics", "naics_code", "code", "NAICS code"),
    }
    for key, (slug, dim, code_field, label) in ranked.items():
        rows = _results(responses[key], key)
        if len(rows) < TOP_N:
            raise GoldenError(f"spending_by_category/{key} returned {len(rows)} rows")
        for rank, row in enumerate(rows[:TOP_N], start=1):
            code = row.get(code_field)
            if not code:
                raise GoldenError(f"spending_by_category/{key} row {rank} has no {code_field}")
            add(
                key,
                f"obligations.{slug}.{code}",
                f"Net contract obligations of {label} {code} ({row.get('name')}), "
                f"rank {rank} by the publisher",
                money(row.get("amount")),
                "USD",
                f"results[{code_field}={code}].amount",
                {dim: code, "rank": rank},
            )

    for row in _results(responses["awarding_subagency"], "awarding_subagency"):
        name = row.get("name")
        add(
            "awarding_subagency",
            "obligations.sub_agency." + "_".join(str(name).lower().split()),
            f"Net contract obligations awarded by the sub-agency {name}",
            money(row.get("amount")),
            "USD",
            f"results[name={name}].amount",
            {"sub_agency_name": name},
        )

    states = sorted(
        _results(responses["pop_state"], "spending_by_geography"),
        key=lambda row: money(row.get("aggregated_amount")),
        reverse=True,
    )
    for rank, row in enumerate(states[:TOP_N], start=1):
        code = row.get("shape_code")
        add(
            "pop_state",
            f"obligations.pop_state.{code}",
            f"Net contract obligations performed in {row.get('display_name')} ({code}), "
            f"rank {rank} of the states in the response",
            money(row.get("aggregated_amount")),
            "USD",
            f"results[shape_code={code}].aggregated_amount",
            {"pop_state_code": code, "rank": rank},
        )
    return values


# Derived values: the SQL runs over the table ``snapshot`` (the Parquet of the manifest).
SQL_AWARDS = "select count(distinct contract_award_unique_key) from snapshot"
SQL_NET_NEGATIVE = (
    "select count(*) from (select contract_award_unique_key, "
    "sum(federal_action_obligation) as net from snapshot "
    "group by contract_award_unique_key) where net < 0"
)
SQL_DEOBLIGATIONS = (
    "select sum(federal_action_obligation) from snapshot where federal_action_obligation < 0"
)
SQL_AVERAGE = (
    "select sum(federal_action_obligation), count(distinct contract_award_unique_key) from snapshot"
)
SQL_TOP10 = (
    "with by_reporting as (select coalesce(recipient_parent_uei, recipient_uei) as reporting_uei, "
    "sum(federal_action_obligation) as net from snapshot group by 1), "
    "ranked as (select net, row_number() over (order by net desc, reporting_uei) as rank "
    "from by_reporting) "
    "select (select sum(net) from ranked where rank <= 10), "
    "(select sum(federal_action_obligation) from snapshot)"
)
SQL_MOST_NEGATIVE = (
    "select contract_award_unique_key, sum(federal_action_obligation) as net from snapshot "
    "group by contract_award_unique_key order by net, contract_award_unique_key limit 1"
)
SQL_COMPETITION = (
    "select extent_competed_code, sum(federal_action_obligation) from snapshot "
    "group by extent_competed_code order by extent_competed_code nulls last"
)


def derived_values(con: duckdb.DuckDBPyConnection, source: dict, day: str) -> list[dict]:
    """The values computed from the snapshot records, tagged ``derived``."""
    values: list[dict] = []

    def add(vid, description, value, unit, sql, formula=None, dims=None):
        derivation = {"sql": sql}
        if formula:
            derivation["formula"] = formula
        values.append(
            _value(
                vid,
                "derived",
                description,
                value,
                unit,
                source,
                day,
                dimensions=dims,
                derivation=derivation,
            )
        )

    (awards,) = con.execute(SQL_AWARDS).fetchone()
    add(
        "awards.active_in_period.count",
        "Distinct contract awards with at least one transaction in the period (ADR-0002)",
        int(awards),
        "awards",
        SQL_AWARDS,
    )
    (negative,) = con.execute(SQL_NET_NEGATIVE).fetchone()
    add(
        "awards.net_negative.count",
        "Awards whose net obligation in the period is below zero",
        int(negative),
        "awards",
        SQL_NET_NEGATIVE,
    )
    (deobligated,) = con.execute(SQL_DEOBLIGATIONS).fetchone()
    add(
        "obligations.deobligations.sum",
        "Sum of the negative transaction obligations (de-obligations) in the period",
        money(deobligated),
        "USD",
        SQL_DEOBLIGATIONS,
    )
    total, award_count = con.execute(SQL_AVERAGE).fetchone()
    average = (Decimal(total) / Decimal(award_count)).quantize(CENT, rounding=ROUND_HALF_EVEN)
    add(
        "awards.average_net_obligation",
        "Net obligations divided by the awards active in the period",
        average,
        "USD",
        SQL_AVERAGE,
        f"round_half_even({money(total)} / {award_count}, 2)",
    )
    top10, total = con.execute(SQL_TOP10).fetchone()
    share = (Decimal(top10) / Decimal(total)).quantize(RATIO_PLACES, rounding=ROUND_HALF_EVEN)
    add(
        "recipients.top10_reporting_share",
        "Share of net obligations held by the 10 largest recipients at the reporting level "
        "(parent UEI, else the recipient's own UEI; ADR-0002)",
        share,
        "ratio",
        SQL_TOP10,
        f"round_half_even({money(top10)} / {money(total)}, 6)",
    )
    award_key, net = con.execute(SQL_MOST_NEGATIVE).fetchone()
    add(
        "award.most_negative.net_obligation",
        "Net obligation in the period of the award with the lowest net obligation",
        money(net),
        "USD",
        SQL_MOST_NEGATIVE,
        dims={"award_key": award_key},
    )
    for code, amount in con.execute(SQL_COMPETITION).fetchall():
        label = code if code is not None else "unreported"
        add(
            f"obligations.extent_competed.{label}",
            "Net contract obligations with extent competed code "
            + (code if code is not None else "missing at the source"),
            money(amount),
            "USD",
            SQL_COMPETITION,
            dims={"competition_code": code},
        )
    return values


def load_snapshot(snapshot_dir: Path, day: str) -> tuple[dict, duckdb.DuckDBPyConnection]:
    """Check the manifest (same day, sha256) and load the Parquet as table ``snapshot``."""
    manifest_path = snapshot_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise GoldenError(f"no snapshot manifest at {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("capture_date_utc") != day:
        raise GoldenError(
            f"the snapshot was captured on {manifest.get('capture_date_utc')}, golden values "
            f"would be captured on {day}; ADR-0001 never mixes days, so run the fetch again first"
        )
    files = manifest.get("files") or []
    if len(files) != 1:
        raise GoldenError("the manifest must list exactly one Parquet file")
    name = files[0]["path"]
    parquet = (snapshot_dir / name).resolve()
    if parquet.parent != snapshot_dir.resolve() or not parquet.is_file():
        raise GoldenError(f"the manifest names a file outside the snapshot directory: {name}")
    if sha256_file(parquet) != files[0]["sha256"]:
        raise GoldenError(f"{name} does not match the sha256 in the manifest")
    con = duckdb.connect()
    con.execute("create table snapshot as select * from read_parquet(?)", [str(parquet)])
    return manifest, con


def reconcile(snapshot: dict[str, object], publisher: list[dict]) -> dict:
    """Compare the snapshot's row count and obligation sum with each publisher figure."""
    checks = []
    for item in publisher:
        ours = snapshot[item["measure"]]
        theirs = item["value"]
        if isinstance(ours, Decimal) or isinstance(theirs, Decimal):
            ours, theirs = Decimal(ours).quantize(CENT), Decimal(theirs).quantize(CENT)
        difference = ours - theirs
        checks.append(
            {
                "measure": item["measure"],
                "snapshot": f"{ours}",
                "publisher": f"{theirs}",
                "difference": f"{difference}",
                "publisher_source": item["source"],
                "match": difference == 0,
            }
        )
    return {"status": "match" if all(c["match"] for c in checks) else "mismatch", "checks": checks}


def publisher_totals(responses: dict[str, dict], by_key: dict[str, Call]) -> list[dict]:
    """Every publisher figure for the row count and the obligation sum, from three endpoints."""
    categories = {
        row.get("category"): row for row in responses["obligations_by_award_category"]["results"]
    }
    agency = responses["agency_awards"]
    awarding = [
        row
        for row in responses["awarding_agency"]["results"]
        if row.get("name") == spec.AGENCY_NAME
    ]
    if len(awarding) != 1:
        raise GoldenError("spending_by_category/awarding_agency does not return the agency once")

    def src(key: str, path: str) -> dict:
        return {**by_key[key].source(), "response_path": path}

    return [
        {
            "measure": "row_count",
            "value": count(responses["transaction_count"]["results"]["contracts"]),
            "source": src("transaction_count", "results.contracts"),
        },
        {
            "measure": "row_count",
            "value": count(agency.get("transaction_count")),
            "source": src("agency_awards", "transaction_count"),
        },
        {
            "measure": "obligation_sum",
            "value": money(categories["contracts"]["aggregated_amount"]),
            "source": src(
                "obligations_by_award_category", "results[category=contracts].aggregated_amount"
            ),
        },
        {
            "measure": "obligation_sum",
            "value": money(agency.get("obligations")),
            "source": src("agency_awards", "obligations"),
        },
        {
            "measure": "obligation_sum",
            "value": money(awarding[0].get("amount")),
            "source": src(
                "awarding_agency", "results[name=Environmental Protection Agency].amount"
            ),
        },
    ]


def run(
    out_dir: Path,
    snapshot_dir: Path,
    client: Client,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict:
    """Capture, derive, reconcile and write ``golden.json``; return its content."""
    captured_at = now()
    day = captured_at.date().isoformat()
    manifest, con = load_snapshot(snapshot_dir, day)
    try:
        plan = calls()
        by_key = {call.key: call for call in plan}
        responses = {call.key: call.send(client) for call in plan}
        if now().date().isoformat() != day:
            raise GoldenError("the UTC day changed during the capture; run fetch and golden again")
        source = {
            "endpoint": manifest["source"]["endpoint"],
            "method": "POST",
            "query": None,
            "request_body": manifest["source"]["request_body"],
        }
        values = owner_values(responses, by_key, day) + derived_values(con, source, day)
        rows, total = con.execute(
            "select count(*), sum(federal_action_obligation) from snapshot"
        ).fetchone()
    finally:
        con.close()
    if not MIN_VALUES <= len(values) <= MAX_VALUES:
        raise GoldenError(f"{len(values)} golden values, the brief asks for 30 to 50")
    ids = [value["id"] for value in values]
    if len(set(ids)) != len(ids):
        raise GoldenError("two golden values share an id")
    reconciliation = reconcile(
        {"row_count": rows, "obligation_sum": Decimal(total)}, publisher_totals(responses, by_key)
    )
    golden = {
        "schema_version": 1,
        "capture_date_utc": day,
        "captured_at_utc": captured_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "snapshot": {
            "manifest": MANIFEST_NAME,
            "capture_date_utc": manifest["capture_date_utc"],
            "file": manifest["files"][0]["path"],
            "sha256": manifest["files"][0]["sha256"],
        },
        "filters": spec.filters(),
        "format": (
            "Amounts are decimal strings in US dollars with two places, counts are integers, "
            "ratios are decimal strings with six places. owner_api values were read from the "
            "endpoint with the request shown; derived values were computed from the snapshot "
            "by the SQL shown, the snapshot itself coming from the request shown."
        ),
        "reconciliation": reconciliation,
        "counts": {
            "values": len(values),
            "owner_api": sum(v["tag"] == "owner_api" for v in values),
            "derived": sum(v["tag"] == "derived" for v in values),
        },
        "values": values,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    partial = out_dir / (GOLDEN_NAME + ".partial")
    partial.write_text(json.dumps(golden, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, out_dir / GOLDEN_NAME)
    return golden


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="directory for golden.json")
    parser.add_argument("--snapshot", type=Path, default=Path("data/snapshot"))
    args = parser.parse_args(argv)
    try:
        golden = run(args.out, args.snapshot, Client())
    except (GoldenError, HttpError) as error:
        print(f"golden stopped: {error}", file=sys.stderr)
        return 1
    counts = golden["counts"]
    print(f"capture date (UTC): {golden['capture_date_utc']}")
    print(
        f"values: {counts['values']} ({counts['owner_api']} owner_api, {counts['derived']} derived)"
    )
    for check in golden["reconciliation"]["checks"]:
        print(
            f"{check['measure']}: snapshot {check['snapshot']}, publisher {check['publisher']}, "
            f"difference {check['difference']} ({check['publisher_source']['endpoint']})"
        )
    if golden["reconciliation"]["status"] != "match":
        print("the snapshot does not reconcile to the publisher's totals", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
