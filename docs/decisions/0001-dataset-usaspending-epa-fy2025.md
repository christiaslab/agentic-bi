# ADR-0001: Dataset, USAspending.gov EPA FY2025 contracts

Status: Accepted, 2026-10-27 (owner decision 5). Recorded in the repository 2026-11-01.

## Context

The project needs a public, keyless, licence-clean dataset whose publisher also exposes aggregate totals, so that a golden set can be built from figures the project did not compute itself. Procurement and spend analytics match the owner's last role. Retail and Greek datasets were ruled out.

## Decision

1. **Source.** The USAspending.gov API (keyless). US Government work, public domain.
2. **Slice.** Awarding agency toptier code `068` (EPA), award types `A`, `B`, `C`, `D` (contracts), `action_date` from 2024-10-01 to 2025-09-30 (US fiscal year 2025). The awarding-agency filter is the one used for the reference figures below; a funding-agency filter gives a different slice. The exact filter JSON is written once in the fetch script and copied into the snapshot manifest.
3. **Grain.** One row per contract transaction (an action or modification). Award-level cumulative columns (for example `total_dollars_obligated`, `current_total_value_of_award`) are not used for measures, since they repeat on every transaction of an award.
4. **Snapshot.** Built from a live API transaction download on the capture day. Column-pruned Parquet plus a manifest: capture date (UTC), filter JSON, field list, row count, sha256 of each file. The dated monthly archives are not used, because they break the same-day rule.
5. **Golden values.** Captured on the same day as the snapshot from the publisher's aggregate endpoints (for example `/api/v2/agency/068/obligations_by_award_category/`, award counts, `spending_by_category` by recipient, PSC, NAICS and sub-agency, by fiscal month) into `docs/golden/`. Each value is tagged `owner_api` (read from an endpoint) or `derived` (computed from records, for example the competition rate or the top-10 supplier share). Snapshot and golden values from different days are never mixed.
6. **Columns.** An explicit allow-list in the fetch script. Excluded: recipient phone and fax numbers, recipient address lines, and every `highly_compensated_officer_*` name and amount. Sole proprietors appear as recipients; their names stay, their contact details do not.
7. **Schema drift.** USAspending is renaming some `transaction_*` fields. The fetch script pins its field list and fails loudly when a field is missing or renamed.

## Reference figures

Measured 2026-10-27 and re-checked 2026-10-30 with the awarding-agency filter:

| Figure | Value |
| --- | --- |
| Transactions (A, B, C, D) | 12,687 |
| Contract obligations | 1,697,318,332.65 |
| IDV obligations (outside A, B, C, D) | 16,178,133.06 |

FY2025 figures still move with late corrections. A later capture may differ; it must then agree with the publisher's totals of its own day, to the cent.

## Consequences

- The golden values share the database with the records. They test the semantic layer and the compiler, not the quality of the source. The README says so.
- Snapshot storage is decided in S1 on the measured size: commit the pruned Parquet when it is under about 10 MB (deterministic CI), otherwise fetch it in CI and check the sha256.
- A data notice in the README names the source, the capture date and the public-domain status.
