# Metrics catalog and compiler

Status: built in S3a (2026-11-01) for [ADR-0002](decisions/0002-structured-query-and-compiler.md). The catalog file is [catalog/metrics.yaml](../catalog/metrics.yaml); the code is in `src/agentic_bi/semantic/`. This page explains both in plain words. When this page and the YAML disagree, the YAML wins, and a test fails until this page names every metric, dimension, grain and operator again.

## How a question becomes a number

1. The agent writes a **structured query**, never SQL.
2. `parse_query` checks its shape against the JSON schema in `query.py`.
3. `compile` looks every name up in the catalog and refuses anything it cannot find.
4. The compiler writes SQL over the tables of the [star-schema contract](contracts/star-schema.md). Every value from the query goes into a separate parameter list.
5. `execute` runs that SQL on a read-only, locked DuckDB connection, with a row limit and a timeout.

## The structured query

| Field | Required | Meaning |
| --- | --- | --- |
| `metric` | yes | One metric name from the catalog |
| `dimensions` | no, default `[]` | Dimensions to group by, in output order |
| `filters` | no, default `[]` | A list of `{field, op, value}`; all of them must hold |
| `grain` | no, default `total` | The time breakdown |
| `time_range` | no, default `null` | `{start, end}` as ISO dates, both ends included; `null` means every date held |
| `order_by` | no, default `null` | `{field, direction}`: rank the rows by the metric (its name) or by one of the query's `dimensions`, `asc` or `desc` |
| `limit` | no, default `null` | Keep the first 1 to 100 rows in the `order_by` order; needs `order_by` |

Example: net obligations by fiscal month and award type, for award types A and B in fiscal year 2025.

```json
{
  "metric": "obligations",
  "dimensions": ["award_type"],
  "grain": "fiscal_month",
  "filters": [{"field": "award_type", "op": "in", "value": ["A", "B"]}],
  "time_range": {"start": "2024-10-01", "end": "2025-09-30"}
}
```

It compiles to this SQL, with the parameters `[2024-10-01, 2025-09-30, "A", "B"]`:

```sql
SELECT
    "d"."fiscal_year" AS "fiscal_year",
    "d"."fiscal_month" AS "fiscal_month",
    "f"."award_type_code" AS "award_type",
    sum("f"."obligation_amount") AS "obligations"
FROM "fct_contract_transaction" AS "f"
LEFT JOIN "dim_date" AS "d" ON "d"."date" = "f"."action_date"
WHERE "f"."action_date" BETWEEN ? AND ?
    AND "f"."award_type_code" IN (?, ?)
GROUP BY "d"."fiscal_year", "d"."fiscal_month", "f"."award_type_code"
ORDER BY "fiscal_year" ASC NULLS LAST, "fiscal_month" ASC NULLS LAST, "award_type" ASC NULLS LAST
```

The output columns are the grain's columns, then the dimensions, then the metric.

### Ordering and top-N

Rows come in the default order: the grain's columns, then the dimensions, ascending, with empty (null) values last. `order_by` and `limit` were added by the [amendment to ADR-0002](decisions/0002-structured-query-and-compiler.md#amendment-2026-11-02-ordering-and-a-row-limit) so that top-N questions can be asked:

- `order_by` puts its key first and keeps the default keys after it as tie-breakers, so a tie always comes out in the same order and a `limit` always keeps the same rows. Empty (null) values of the ranked key come last in both directions.
- `order_by.field` is the metric's name or one of the query's `dimensions`, the output columns a row can be ranked by. A grain column, another metric or a physical column is refused as `invalid_query` with the field `order_by.field`. Time order needs no `order_by`: it is the default.
- `limit` is an integer from 1 to 100 and needs `order_by`, so the rows it keeps are always the ones named, never the first ones by accident. Anything else is refused as `invalid_query` with the field `limit`. A `limit` above the number of groups returns them all.
- The `limit` is bound as a parameter like every value: the SQL says `LIMIT ?`.
- A query without `order_by` and `limit` compiles exactly as before the amendment.

Example: the five recipients (at the parent level) with the largest net obligations in fiscal year 2025.

```json
{
  "metric": "obligations",
  "dimensions": ["recipient"],
  "time_range": {"start": "2024-10-01", "end": "2025-09-30"},
  "order_by": {"field": "obligations", "direction": "desc"},
  "limit": 5
}
```

It compiles to this SQL, with the parameters `[2024-10-01, 2025-09-30, 5]`:

```sql
SELECT
    "f"."reporting_uei" AS "recipient",
    sum("f"."obligation_amount") AS "obligations"
FROM "fct_contract_transaction" AS "f"
WHERE "f"."action_date" BETWEEN ? AND ?
GROUP BY "f"."reporting_uei"
ORDER BY "obligations" DESC NULLS LAST, "recipient" ASC NULLS LAST
LIMIT ?
```

## Metrics

| Metric | What it measures | Result |
| --- | --- | --- |
| `obligations` | Net federal action obligations: the plain sum of every transaction's obligation amount. De-obligations are negative, so an award, a month or a group can be net negative. | US dollars, exact to the cent |
| `award_count` | Distinct awards active in the period: awards with at least one transaction in the selection, whatever its amount. Not the number of new awards. | A whole number |
| `avg_award_value` | `obligations` divided by `award_count` over the same selection. | US dollars, as a decimal fraction |
| `supplier_concentration_top10` | Share of net obligations held by the ten largest recipients, counted at the parent level. | A fraction; 1 when ten or fewer recipients are present |
| `competition_rate` | Share of net obligations on transactions whose extent-competed code counts as competed. Transactions with no code count as not competed. | A fraction |

Points worth knowing before reading a result:

- **Recipient level.** The `recipient` dimension and the concentration metric use the reporting UEI: the parent's UEI when a parent is recorded, otherwise the recipient's own. A subsidiary's own UEI never appears, and filtering on it matches nothing.
- **Awards in several groups.** Grouped by fiscal month, or by a field that can change within an award, an award active in two groups counts once in each. The groups of `award_count` can therefore add up to more than its total.
- **Shares above 1.** Because de-obligations are negative, the top-ten share can exceed 1 when the remaining recipients are net negative. A tie at tenth place is broken by UEI and cannot change the share.
- **Empty selections.** A `total` query that matches no transaction returns one row: `obligations` and the ratios are empty (null), `award_count` is 0. A grouped query returns no row for an empty group.

Allowed dimensions per metric (grouping and filtering alike):

| Dimension | obligations | award_count | avg_award_value | supplier_concentration_top10 | competition_rate |
| --- | --- | --- | --- | --- | --- |
| `award_type` | yes | yes | yes | yes | yes |
| `sub_agency` | yes | yes | yes | yes | yes |
| `psc` | yes | yes | yes | yes | yes |
| `naics` | yes | yes | yes | yes | yes |
| `competition` | yes | yes | yes | yes | no |
| `competed` | yes | yes | yes | yes | no |
| `recipient` | yes | yes | yes | no | yes |
| `pop_state` | yes | yes | yes | yes | yes |

The two "no" cells are rules, not gaps: concentration by recipient would always be 1, and the competition rate by competition code or by `competed` would always be 0 or 1. The loader refuses a catalog that allows either.

## Grains

| Grain | Output columns | Meaning |
| --- | --- | --- |
| `total` | none | One row for the whole selection |
| `fiscal_year` | `fiscal_year` | Fiscal year Y runs from 1 October of Y-1 to 30 September of Y |
| `fiscal_quarter` | `fiscal_year`, `fiscal_quarter` | Quarter 1 is October to December |
| `fiscal_month` | `fiscal_year`, `fiscal_month` | Month 1 is October, month 12 is September |

Fiscal months and quarters always come with their fiscal year, so October 2024 and October 2025 never merge. Time breakdowns are only grains: `fiscal_month` in `dimensions` is refused with a hint to use `grain`, and dates are filtered with `time_range`, not with a filter.

## Dimensions

| Dimension | Meaning | Values in a filter |
| --- | --- | --- |
| `award_type` | Contract award type: A a BPA call, B a purchase order, C a delivery order, D a definitive contract | only `A`, `B`, `C`, `D` |
| `sub_agency` | Awarding sub-agency code | any text |
| `psc` | Product and Service Code | any text |
| `naics` | NAICS industry code | any text |
| `competition` | Extent-competed code as recorded at the source | any text |
| `competed` | Whether that code counts as competed; null when the transaction has no code | `true` or `false` |
| `recipient` | Reporting recipient UEI (parent level, see above) | any text |
| `pop_state` | Place-of-performance state or territory code; null when performed abroad | any text |

Codes are text: a NAICS code given as a number is refused, so that `"054"` and `54` can never be confused.

## Operators

| Operator | Value | Meaning |
| --- | --- | --- |
| `eq` | one value | The field equals the value |
| `ne` | one value | The field differs from the value; rows where the field is null (for example work abroad) are kept |
| `in` | a list of 1 to 100 values | The field equals one of them |
| `not_in` | a list of 1 to 100 values | The field equals none of them; rows where the field is null are kept |

## Refusals

A refusal is an error the agent reports as it is; it never substitutes another metric or dimension (ADR-0002 point 3). Each refusal has a stable `code` and the `field` of the query it concerns, written as a path such as `filters[1].op`.

| Code | Raised when |
| --- | --- |
| `invalid_query` | The query does not match the JSON schema: a missing or extra field, a wrong type, a repeated dimension |
| `malformed_time_range` | `time_range` is not `{start, end}` with two real ISO dates (`YYYY-MM-DD`) and start on or before end |
| `unknown_metric` | The metric is not in the catalog |
| `unknown_grain` | The grain is not in the catalog |
| `unknown_dimension` | A name in `dimensions` is not a catalog dimension |
| `dimension_not_allowed` | The dimension exists but is not allowed for the metric, in `dimensions` or in a filter |
| `filter_field_not_dimension` | A filter names something that is not a catalog dimension, such as a physical column or a metric |
| `unknown_operator` | A filter operator is not in the catalog |
| `invalid_filter_value` | A filter value has the wrong type, a list where one value is expected or the reverse, or lies outside a dimension's fixed values |

## What the compiler guarantees

- **No value in SQL text.** Filter values and the dates of the time range are `?` placeholders with the values in the parameter list. The compiler never writes a string literal, so compiled SQL holds no quote character and no semicolon; a test checks this with hostile values for every metric.
- **Only contract names.** Every table and column comes from the catalog, which the loader checks against `contract.py`; a test checks `contract.py` against the contract document. Names in the query only select among catalog entries.
- **No dropped rows.** Dimensions join with LEFT joins, and only the joins a query needs are written.
- **One SELECT only.** The runner parses the SQL with DuckDB and refuses anything but a single SELECT statement, as a second line behind the compiler.

## The query connection

`open_query_connection` opens an existing DuckDB file read-only with `enable_external_access=false`, `autoinstall_known_extensions=false`, `autoload_known_extensions=false` and `lock_configuration=true`, so these settings cannot be changed afterwards. `execute` checks the connection has these settings before running, and applies a row limit (10,000 by default; more rows is an error, not a silent cut) and a timeout (30 seconds by default).

DuckDB keeps the open database's own files on an internal allow-list even with external access disabled: they can still be read through file functions, and in DuckDB 1.5.6 a `COPY ... TO` naming the database's `.wal` path succeeds. No structured query can produce either, since the compiler writes only SELECT statements and the runner refuses anything else.

## Changing the catalog

Edit `catalog/metrics.yaml`, then run the tests. The loader refuses an unknown key, a column outside the contract, a reference to a missing metric or dimension, a name that is not a lowercase identifier, a metric allowed to group by its own entity or condition, and a name used twice across grains, dimensions and metrics. A new metric kind or operator needs compiler code first, then a catalog entry.
