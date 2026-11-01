# Warehouse

Status: written in S2, 2026-11-01. Builds [the star-schema contract](contracts/star-schema.md) from the snapshot of [docs/sources.md](sources.md). Serves [ADR-0001](decisions/0001-dataset-usaspending-epa-fy2025.md) and [ADR-0002](decisions/0002-structured-query-and-compiler.md).

## Build

```sh
uv run sqlmesh -p warehouse plan --auto-apply --no-prompts
```

The command reads `data/snapshot/contract_transactions.parquet` and nothing else, and writes `data/tmp/warehouse.duckdb` (gitignored). A second run with no change answers "No changes to plan". Every audit blocks: a failed audit stops the plan and the `prod` views are not updated. From Python, `agentic_bi.warehouse.build(path)` builds the same project into another file; the tests use it.

SQLMesh 0.236.4 (PyPI), on DuckDB 1.5.6. Its anonymous usage analytics are turned off in the project configuration, since the project's only network is package installs and the USAspending hosts (AGENTS.md).

## Layout

| Path | Content |
| --- | --- |
| `warehouse/config.py` | loads the configuration from `src/agentic_bi/warehouse/` |
| `warehouse/models/staging/contract_transaction.sql` | `staging.contract_transaction`: the snapshot, typed, one row per transaction, with the two per-transaction derivations below |
| `warehouse/models/seeds/extent_competed.sql`, `warehouse/seeds/extent_competed.csv` | `seeds.extent_competed`: the competition seed |
| `warehouse/models/star/` | the eight contract tables |
| `warehouse/audits/` | `foreign_key` and `competition_seed_matches_source` |

## What the database holds

| Schema | Content |
| --- | --- |
| `main` | exactly the eight tables of the contract, with its column names, order and types; a test compares them |
| `staging`, `seeds` | the staged snapshot and the seed, which the contract tables are built from |
| `sqlmesh`, `sqlmesh__*` | SQLMesh's state and its physical tables |

The eight contract tables are SQLMesh views over physical tables in the same file. Every model is materialised, so the file never reads the Parquet again: a read-only connection with `enable_external_access=false` and `lock_configuration=true` (ADR-0002 point 6) queries it. The views name the file's catalog (`warehouse`), so keep the file name when copying it.

Built from the 2026-11-01 snapshot:

| Table | Rows |
| --- | --- |
| `fct_contract_transaction` | 12,687 |
| `dim_date` | 365 |
| `dim_recipient` | 1,165 |
| `dim_reporting_recipient` | 1,132 |
| `dim_sub_agency` | 1 |
| `dim_psc` | 342 |
| `dim_naics` | 226 |
| `dim_competition` | 6 |

## Rules

- **Reporting parent.** `fct_contract_transaction.reporting_uei` is `coalesce(recipient_parent_uei, recipient_uei)` on each transaction, the rule of the derived top-10 share in `docs/golden/golden.json`. On 2026-11-01 every transaction records a parent, and 13 recipients change parent during the year.
- **Names and labels.** Each dimension keeps one row per key. Its name or label is the one recorded on the latest transaction with that key, ties broken by the greatest `transaction_key`. The contract fixes this rule for `dim_reporting_recipient` (the parent name, or the recipient name when the fallback applies); the other dimensions use it too. On 2026-11-01 no key carries two names, so the rule changes no value; a synthetic test proves it.
- **Place of performance.** `pop_state_code` is the source state when the country is `USA`, else null (docs/sources.md). It is null on 10 transactions of 2026-11-01, all abroad or in `UMI`, where the source gives no state.
- **Dates.** `dim_date` is every day of fiscal year 2025, 2024-10-01 to 2025-09-30, generated from the fiscal year of the slice, not from the transactions. October is fiscal month 1; quarters follow the fiscal months. The source's `action_date_fiscal_year` agrees on every transaction (tested).
- **No invented rows.** `dim_competition` holds only the codes found in the snapshot. The 5 transactions with no competition code keep a null `competition_code`; no dimension row stands for them.

## Competition seed

`warehouse/seeds/extent_competed.csv` lists the extent-competed codes found in the snapshot: A, B, C, D, F and G. These are also the codes of the derived golden values `obligations.extent_competed.*`.

| Code | Label | `is_competed` |
| --- | --- | --- |
| A | FULL AND OPEN COMPETITION | true |
| B | NOT AVAILABLE FOR COMPETITION | false |
| C | NOT COMPETED | false |
| D | FULL AND OPEN COMPETITION AFTER EXCLUSION OF SOURCES | true |
| F | COMPETED UNDER SAP | true |
| G | NOT COMPETED UNDER SAP | false |

Source of the mapping:

1. **The codes and labels** are the publisher's: the `extent_competed_code` and `extent_competed` fields of the 2026-11-01 snapshot (USAspending, from FPDS). The audit `competition_seed_matches_source` fails the build when a code of the snapshot is missing from the seed or carries another label.
2. **The meaning of each code** is the FPDS-NG Data Element Dictionary, element 10A "Extent Competed" (GSA, [fpds.gov](https://www.fpds.gov)). A and D are full and open competition, with or without the exclusion of sources (FAR Part 6, subparts 6.1 and 6.2); F is competition under simplified acquisition procedures (FAR Part 13); B, C and G are actions not available for competition, not competed, or not competed under simplified acquisition procedures.
3. **`is_competed`** is true for the competed codes A, D and F and false for B, C and G. Each label states it: the three labels that open with "NOT" are the three codes marked false, and a test checks that agreement.

The FPDS dictionary and the FAR were cited from knowledge, not fetched in this session (the session's network allows package installs and the USAspending hosts only). The owner should confirm the edition and link before the mapping is quoted outside the repository. The codes E, CDO and NDO of the same element do not occur in the snapshot and are not in the seed; a snapshot that brings one fails the build until the seed lists it.

## Audits

| Model | Audit |
| --- | --- |
| `fct_contract_transaction` | `transaction_key` not null and unique; `award_type_code` in A, B, C, D; `foreign_key` on each of the seven keys below |
| each dimension | its key not null and unique; names and labels not null |
| `dim_date` | fiscal month 1 to 12, fiscal quarter 1 to 4 |
| `dim_competition` | `is_competed` not null (every code is in the seed); `competition_seed_matches_source` |
| `seeds.extent_competed` | not null and unique code |
| `staging.contract_transaction` | awarding agency code `068` (the slice) |

`foreign_key` fails on a fact row whose key is not null and has no row in its dimension; a null key passes and is counted instead. The fact declares the dimensions as dependencies so they are built before the audit reads them.

| Fact column | Dimension | Null rows, 2026-11-01 |
| --- | --- | --- |
| `action_date` | `dim_date.date` | 0 |
| `recipient_uei` | `dim_recipient` | 0 |
| `reporting_uei` | `dim_reporting_recipient` | 0 |
| `sub_agency_code` | `dim_sub_agency` | 0 |
| `psc_code` | `dim_psc` | 0 |
| `naics_code` | `dim_naics` | 0 |
| `competition_code` | `dim_competition` | 5 |

## Tests

`tests/warehouse/` builds the project into a temporary DuckDB file and reads it through the ADR-0002 connection. It checks the contract tables and columns, the row count and obligation sum against `data/snapshot/manifest.json`, every dimensional `owner_api` golden value through its dimension (the 12 fiscal months, recipients, PSC, NAICS, sub-agency, states), the derived top-10 reporting share and the obligations per competition code, to the cent. A small synthetic snapshot, built through the same project, proves the rules above and makes the audits fail on a duplicate or null key, a date outside the fiscal year, a code missing from the seed and a relabelled code.
