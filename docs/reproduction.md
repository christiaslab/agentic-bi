# Golden reproduction

Status: written in S3b, 2026-11-02. Proves that the compiler of [ADR-0002](decisions/0002-structured-query-and-compiler.md), run on the S2 warehouse ([docs/warehouse.md](warehouse.md)), gives the publisher's golden values of [docs/golden/golden.json](golden/golden.json) ([ADR-0001](decisions/0001-dataset-usaspending-epa-fy2025.md) point 5). This is the deterministic half of the eval harness (plan row S5b): no model, no key, no network.

## Result on the 2026-11-01 capture

| Outcome | Values |
| --- | --- |
| Reproduced | 43 of 48: 33 `owner_api`, 10 `derived` |
| Differs, known | 1: `obligations.recipient.Z4XGALW7L9K6` (below) |
| Not expressible | 4 (table below) |
| Errors | 0 |

Reproduced: the slice total, the 12 fiscal months, 4 of the 5 recipients, the 5 product and service codes, the 5 NAICS codes, the sub-agency, the 5 states, the awards active in the period, the average net obligation per award, the top-10 reporting share and the obligations of each of the 7 extent-competed codes, including the missing code (-47,387.44). Amounts and counts are equal to the cent; the two ratios are within the tolerance below.

## How a golden value is mapped

[evals/golden_queries.yaml](../evals/golden_queries.yaml) lists every golden id exactly once. An entry is either:

- a **query**: one structured query of the S3a schema ([docs/catalog.md](catalog.md)), compiled from the catalog and run on the read-only query connection exactly as an agent's query would be, through `agentic_bi.semantic.execute`. Nothing in the reproduction writes SQL; or
- **not expressible**: a one-line reason, a disposition (a candidate catalog change, or out of scope by design), and an attempt: the nearest structured query and the refusal code the compiler gives it. The run compiles the attempt every time, so the day the catalog learns to express the value, the entry turns red and is due for a query.

Choices made in the mapping:

| Golden values | Structured query | Why |
| --- | --- | --- |
| Slice total, awards active, average per award, top-10 share | the metric, `grain` `total` | one row |
| 12 fiscal months | `obligations`, `grain` `fiscal_month`, and a `row` `{fiscal_year: 2025, fiscal_month: n}` | one query, as the publisher's `spending_over_time` by month; fiscal months are a grain, not a filter |
| Recipients, PSC, NAICS, states | `obligations` with an `eq` filter on `recipient`, `psc`, `naics` or `pop_state` | one question, one value |
| The sub-agency | `obligations` with `sub_agency` `eq` `6800` | the golden value names the sub-agency, the dimension takes its code; a test checks that `dim_sub_agency` gives 6800 that name |
| 7 extent-competed codes | `obligations` grouped by `competition`, and a `row` `{competition: code}` | one query, as the derivation SQL; a filter cannot select the missing code, because filter values are never null |

Every query carries the fiscal year of the slice as its `time_range`, 2024-10-01 to 2025-09-30, as a question about fiscal year 2025 would. A `row` must name every grouping column of the result, so it can never pick more than one row; picking none is an error.

A value that does not match is never moved to another query to make it pass. Its entry gets a `known_difference` with the reason, the report keeps both numbers, and the test marks it `xfail` with that reason, strictly, so it turns red the day it starts to match.

## Comparison rule

The rule follows the metric's kind in the catalog, never a setting of the mapping, so no entry can loosen its own comparison (`src/agentic_bi/reproduce/compare.py`).

| Metric kind | Metrics | Rule |
| --- | --- | --- |
| `sum` | `obligations` | exact: the compiler returns a DECIMAL(38,2) sum, equal to the golden decimal string to the cent |
| `count_distinct` | `award_count` | exact: an integer equal to the golden integer |
| `ratio`, `top_n_share`, `share_where` | `avg_award_value`, `supplier_concentration_top10`, `competition_rate` | within half a unit of the golden value's last written decimal place, inclusive |

**Why half a unit for ratios.** The compiler returns a ratio as a DOUBLE, the quotient of two exact values. The golden file does not store that quotient: it stores it rounded half-even to six places for shares (`0.449020`) and to the cent for the average award value (`282134.03`), as each value's `derivation.formula` records. A golden value written with p places stands for every number within half a unit of its p-th place, so the reproduced value matches exactly when rounding it to p places can give the golden value:

```text
| reproduced - golden | <= 0.5 * 10^-p
```

A tighter bound would reject a correct quotient because the golden file rounded it; a looser one would accept a different quotient. The bound is inclusive so a value exactly on a half (where half-even and half-up rounding disagree) is not refused for the rounding mode. The DOUBLE's error is near 10^-16 of the value, far below either tolerance (5 * 10^-7 for shares, 0.005 for the average). The reproduced DOUBLE is read as the shortest decimal that reads back as the same double (Python's `repr`), and the difference is written in full.

For the average award value, half a cent is the same thing as "equal to the cent after rounding": the golden figure is the rounded cent of a quotient that does not end.

| Golden id | Golden | Reproduced | Difference | Tolerance |
| --- | --- | --- | --- | --- |
| `awards.average_net_obligation` | 282134.03 | 282134.03135804523 | 0.00135804523 | 0.005 |
| `recipients.top10_reporting_share` | 0.449020 | 0.4490202395623079 | 0.0000002395623079 | 0.0000005 |

## The known difference: recipient level

| Golden id | Golden | Reproduced | Difference |
| --- | --- | --- | --- |
| `obligations.recipient.Z4XGALW7L9K6` | 84,868,589.24 | -923,852.08 | -85,792,441.32 |

The publisher ranks recipients by their own UEI. The catalog's `recipient` dimension is the reporting UEI, the parent's UEI recorded on each transaction (ADR-0002, "Recipient level"; [the star-schema contract](contracts/star-schema.md)). For four of the five recipients the two levels select the same transactions, because each is its own parent on every transaction and has no subsidiary, so the values reproduce. Recipient `Z4XGALW7L9K6` (Weston Solutions Inc) records four different parents during the year:

| Parent (reporting UEI) | Transactions of Z4XGALW7L9K6 | Net obligations |
| --- | --- | --- |
| `ML53DYBW2523` | 115 | 64,253,671.53 |
| `RBRXSHXKFCB5` | 90 | 21,588,769.79 |
| `Z4XGALW7L9K6` | 6 | -923,852.08 |
| `XA9SVRU7K217` | 1 | -50,000.00 |

The four add up to the publisher's 84,868,589.24. The structured query `recipient eq Z4XGALW7L9K6` selects the six transactions whose parent is Z4XGALW7L9K6, which is what the catalog defines. The compiler is right by its catalog; the catalog has no recipient-level dimension. A test proves both points on every run: a recipient reproduces exactly when its own and its reporting transactions are the same set, and the publisher's figure is the plain sum of the recipient's own transactions. Candidate catalog change: a dimension on `fct_contract_transaction.recipient_uei`.

## Not expressible

| Golden id | Tag | Golden | Reason | Disposition |
| --- | --- | --- | --- | --- |
| `transactions.count` | owner_api | 12687 | The catalog counts awards, not transactions; no metric counts transaction rows. | candidate catalog change |
| `awards.net_negative.count` | derived | 1710 | Counting awards by the sign of their net obligation needs a per-award sum and a condition on it; no metric kind does that. | candidate catalog change |
| `obligations.deobligations.sum` | derived | -276256715.93 | The catalog sums obligations net only; no metric or filter selects the negative transaction amounts. | candidate catalog change |
| `award.most_negative.net_obligation` | derived | -4877871.74 | Finding the award with the lowest net obligation needs an award dimension and an order with a limit; the ADR-0002 query has neither, and awards are not a catalog dimension. | out of scope by design |

What each candidate would take, for the owner to weigh (none is built here):

- `transactions.count`: a `count_distinct` metric on `fct_contract_transaction.transaction_key`. A catalog entry only; the compiler already has the kind.
- `awards.net_negative.count`: a new metric kind (count of entities whose net measure is below zero), compiler code first, then the catalog entry. ADR-0002 asks for golden cases with net-negative awards; this would test one through the compiler.
- `obligations.deobligations.sum`: a metric summing only negative amounts, which needs a condition on the amount (a new kind or a sign option on `sum`).
- `award.most_negative.net_obligation` stays out: an award-level answer needs ordering and a limit in the structured query, an ADR-0002 change (S3a report, open question 2).

## Rerun

```sh
uv run python -m agentic_bi.reproduce --out data/tmp/reproduction.json
```

The command builds the SQLMesh project with `agentic_bi.warehouse.build` into `data/tmp/warehouse.duckdb` (a plan with no change is quick), runs every mapped query through the read-only query connection, prints one line per golden id and writes `data/tmp/reproduction.json` (gitignored). `--database PATH` builds and reads another file; keep the name `warehouse.duckdb`.

Each value of the report has `id`, `tag`, `status` (`reproduced`, `differs`, `error`, `not_expressible`), `expected`, `golden`, `reproduced` and `difference` (reproduced minus golden; amounts and ratios as decimal strings, counts as integers). A mapped value also has its `rule`, `tolerance`, structured `query`, `row`, compiled `sql` and bound `params`; a not-expressible value has its `reason`, `disposition` and `attempt`.

Exit status: 0 when every outcome is the expected one, 1 when one is not (a mapped value that differs or fails without a known difference, a known difference that now matches, a not-expressible attempt the compiler no longer refuses), 2 when the mapping or the golden file is malformed.

The tests do the same on a temporary build: `uv run pytest -q tests/reproduce`. `test_reproduce_golden.py` has one test per mapped golden id and fails on any difference.

## Limits

- The golden values share the database with the records (ADR-0001, Consequences): this tests the warehouse, the catalog and the compiler, not the publisher's data.
- The derived values were computed by S1 from the snapshot with plain SQL; reproducing them proves the compiler agrees with that SQL, not with a publisher endpoint.
- Ranks are not checked: the golden `rank` fields are the publisher's ordering, and the structured query has no order or limit.
