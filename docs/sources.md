# Sources

Status: written in S1, 2026-11-01. Serves [ADR-0001](decisions/0001-dataset-usaspending-epa-fy2025.md) and [the star-schema contract](contracts/star-schema.md).

## Publisher

[USAspending.gov](https://www.usaspending.gov), API v2, keyless. US Government work in the public domain.

## Slice

One filter object, defined once in `src/agentic_bi/ingest/spec.py` and sent unchanged to the transaction download and to every search endpoint of the golden capture:

```json
{
  "award_type_codes": ["A", "B", "C", "D"],
  "time_period": [{"start_date": "2024-10-01", "end_date": "2025-09-30", "date_type": "action_date"}],
  "agencies": [{"type": "awarding", "tier": "toptier", "name": "Environmental Protection Agency"}]
}
```

The search filter does not accept a toptier code (the API answers `Unexpected field 'toptier_code'`), so the agency is named. The fetch then checks that every row carries `awarding_agency_code` `068`. The two agency endpoints (`/api/v2/agency/068/...`) take the code in the path and the fiscal year as a parameter.

## Snapshot

| Item | Value |
| --- | --- |
| Endpoint | `POST https://api.usaspending.gov/api/v2/download/transactions/`, then `GET /api/v2/download/status`, then the zip on `files.usaspending.gov` |
| Request | the filter above, `columns` set to the allow-list below, `file_format` `csv` |
| File used | the `Contracts_PrimeTransactions_*.csv` member only; the subaward member is never read (it comes back empty for these columns) |
| Output | `data/snapshot/contract_transactions.parquet` (zstd, sorted by `contract_transaction_unique_key`) and `data/snapshot/manifest.json` |
| Grain | one row per contract transaction (ADR-0001 point 3) |

The download is asked for the allow-listed columns only, so excluded fields never reach the disk. A name the source no longer knows makes the download fail, and the fetch compares the returned header with the pinned list, name by name and in order (ADR-0001 point 7).

## Kept fields

Every field of the allow-list (`spec.FIELDS`), with the star-schema column it feeds. "Unused" fields are kept for checks or for reading the data by eye; no table of the contract takes them.

| Source field | Snapshot type | Star-schema column | Note |
| --- | --- | --- | --- |
| `contract_transaction_unique_key` | VARCHAR | `fct_contract_transaction.transaction_key` | unique, checked by the fetch |
| `contract_award_unique_key` | VARCHAR | `fct_contract_transaction.award_key` | |
| `award_id_piid` | VARCHAR | unused | the award's PIID, for reading |
| `modification_number` | VARCHAR | unused | for reading |
| `federal_action_obligation` | DECIMAL(18,2) | `fct_contract_transaction.obligation_amount` | signed; negative is a de-obligation |
| `action_date` | DATE | `fct_contract_transaction.action_date`, `dim_date.date` | |
| `action_date_fiscal_year` | INTEGER | unused | cross-check for `dim_date.fiscal_year`; always 2025 |
| `awarding_agency_code` | VARCHAR | unused | slice check: always `068` |
| `awarding_sub_agency_code` | VARCHAR | `fct_contract_transaction.sub_agency_code`, `dim_sub_agency.sub_agency_code` | |
| `awarding_sub_agency_name` | VARCHAR | `dim_sub_agency.sub_agency_name` | |
| `recipient_uei` | VARCHAR | `fct_contract_transaction.recipient_uei`, `dim_recipient.recipient_uei` | present on every row |
| `recipient_name` | VARCHAR | `dim_recipient.recipient_name`; `dim_reporting_recipient.reporting_name` when no parent is recorded | sole proprietors' names stay (ADR-0001 point 6) |
| `recipient_parent_uei` | VARCHAR | with `recipient_uei`, `fct_contract_transaction.reporting_uei` and `dim_reporting_recipient.reporting_uei` | present on every row; the reporting parent belongs to the transaction (contract revision of 2026-11-01) |
| `recipient_parent_name` | VARCHAR | `dim_reporting_recipient.reporting_name` | |
| `primary_place_of_performance_country_code` | VARCHAR | used to derive `fct_contract_transaction.pop_state_code` | the state is null when this is not `USA` |
| `primary_place_of_performance_state_code` | VARCHAR | `fct_contract_transaction.pop_state_code` | null on the 10 rows of 2026-11-01 whose country is not `USA`, one of them `UMI` (US Minor Outlying Islands) |
| `award_type_code` | VARCHAR | `fct_contract_transaction.award_type_code` | `A`, `B`, `C` or `D`, checked by the fetch |
| `award_type` | VARCHAR | unused | the award type's label |
| `product_or_service_code` | VARCHAR | `fct_contract_transaction.psc_code`, `dim_psc.psc_code` | |
| `product_or_service_code_description` | VARCHAR | `dim_psc.psc_description` | |
| `naics_code` | VARCHAR | `fct_contract_transaction.naics_code`, `dim_naics.naics_code` | |
| `naics_description` | VARCHAR | `dim_naics.naics_description` | |
| `extent_competed_code` | VARCHAR | `fct_contract_transaction.competition_code`, `dim_competition.competition_code` | null on 5 rows of the 2026-11-01 snapshot |
| `extent_competed` | VARCHAR | `dim_competition.competition_description` | `is_competed` comes from the S2 seed, not from the source |

The fiscal fields of `dim_date` derive from `action_date` (contract rule), not from the source.

## Excluded fields

Never requested, and refused by the fetch and by a test if they ever appear (ADR-0001 point 6): `recipient_phone_number`, `recipient_fax_number`, `recipient_address_line_1`, `recipient_address_line_2`, and `highly_compensated_officer_1_name` to `highly_compensated_officer_5_amount`. The other 259 fields of the 297-column download are simply not on the allow-list.

## Golden values

`src/agentic_bi/ingest/golden.py` writes `docs/golden/golden.json` on the snapshot's capture day and refuses a snapshot of another UTC day. Each value records its endpoint, method, query or request body, capture date and tag.

| Tag | Endpoint | Values |
| --- | --- | --- |
| `owner_api` | `POST /api/v2/search/spending_by_transaction_count/` | transaction count |
| `owner_api` | `GET /api/v2/agency/068/obligations_by_award_category/?fiscal_year=2025` | net contract obligations |
| `owner_api` | `POST /api/v2/search/spending_over_time/` (`group` `month`) | obligations by fiscal month, 12 values (month 1 is October) |
| `owner_api` | `POST /api/v2/search/spending_by_category/recipient/`, `/psc/`, `/naics/` | the top 5 of each by obligations |
| `owner_api` | `POST /api/v2/search/spending_by_category/awarding_subagency/` | obligations by sub-agency (EPA has one) |
| `owner_api` | `POST /api/v2/search/spending_by_geography/` (place of performance, state) | the 5 largest states |
| `derived` | computed from the snapshot by the SQL stored with each value | awards active in the period, awards with net-negative obligations, de-obligations, average net obligation per award, top-10 share at the reporting level, the most negative award, obligations by extent-competed code |

Reconciliation only (not golden values): `GET /api/v2/agency/068/awards/?fiscal_year=2025&award_type_codes=A,B,C,D` and `POST /api/v2/search/spending_by_category/awarding_agency/` give the row count and the obligation sum a second and third time.

Not used: `POST /api/v2/search/spending_by_award_count/` answers 3,203 contract awards for the slice, while the snapshot holds 6,016 distinct awards with a transaction in the period (the ADR-0002 definition). The award search filters on an award-level date, so its count is not the same measure.

## Open points for S2

- **Parent UEI varies within a recipient.** On 2026-11-01, 13 of the 1,165 recipient UEIs carry more than one `recipient_parent_uei` across their transactions. Resolved by the contract revision of 2026-11-01: the fact carries `reporting_uei` per transaction (`coalesce(recipient_parent_uei, recipient_uei)`, as the derived top-10 share in `docs/golden/golden.json` does), and `dim_reporting_recipient` has one row per `reporting_uei`. `dim_recipient` keeps no parent column.
- **Missing competition code.** 5 transactions (net -47,387.44) have no `extent_competed_code`; `dim_competition` needs a row for them or the fact join fails its audit.
