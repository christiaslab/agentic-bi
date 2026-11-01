# Contract: the star schema the semantic layer queries

Status: contract for S1, S2 and S3, written in S0 (2026-11-01). The names below are the project's own. S1 records which source field feeds each column; S2 builds exactly these tables; S3 compiles only against them. A change to a name or a type is a pull request that updates this file first. Revised 2026-11-01 after the S1 review (owner ruling): the reporting parent belongs to the transaction, because a recipient's parent can change during the year.

## Fact

`fct_contract_transaction`, one row per contract transaction (ADR-0001 point 3).

| Column | Type | Meaning |
| --- | --- | --- |
| `transaction_key` | VARCHAR, unique | The transaction's unique key at the source |
| `award_key` | VARCHAR | The award the transaction belongs to |
| `action_date` | DATE | Date of the action |
| `obligation_amount` | DECIMAL(18,2) | Federal action obligation, signed; negative is a de-obligation |
| `award_type_code` | VARCHAR | One of `A`, `B`, `C`, `D` |
| `recipient_uei` | VARCHAR | Joins `dim_recipient` |
| `reporting_uei` | VARCHAR | The parent UEI recorded on the transaction, else `recipient_uei`; joins `dim_reporting_recipient` |
| `sub_agency_code` | VARCHAR | Awarding sub-agency, joins `dim_sub_agency` |
| `psc_code` | VARCHAR | Joins `dim_psc` |
| `naics_code` | VARCHAR | Joins `dim_naics` |
| `competition_code` | VARCHAR | Extent competed, joins `dim_competition` |
| `pop_state_code` | VARCHAR | Place of performance, US state or territory code, null when abroad |

## Dimensions

| Table | Key | Other columns |
| --- | --- | --- |
| `dim_date` | `date` | `fiscal_year`, `fiscal_quarter` (1 to 4), `fiscal_month` (1 = October), `calendar_year`, `calendar_month` |
| `dim_recipient` | `recipient_uei` | `recipient_name` |
| `dim_reporting_recipient` | `reporting_uei` | `reporting_name`: the parent name recorded on the latest transaction with that `reporting_uei` (the recipient name when the fallback applies), ties broken by `transaction_key` |
| `dim_sub_agency` | `sub_agency_code` | `sub_agency_name` |
| `dim_psc` | `psc_code` | `psc_description` |
| `dim_naics` | `naics_code` | `naics_description` |
| `dim_competition` | `competition_code` | `competition_description`, `is_competed` (BOOLEAN) |

The mapping from source competition codes to `is_competed` is a seed with a cited source, added in S2. Until then tests use a fixture seed.

## Rules

- Joins are many-to-one from the fact; a fact row whose key is missing from a dimension fails an audit.
- No personal contact data, officer names or officer compensation in any table (ADR-0001 point 6).
- Fiscal fields derive from `action_date`: fiscal year Y runs from Y-1-10-01 to Y-09-30.
