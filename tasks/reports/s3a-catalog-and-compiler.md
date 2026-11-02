# Report: s3a-catalog-and-compiler

Date: 2026-11-01. Branch: `s3a-catalog-and-compiler`, from `main` at `58520b0`. Not merged; the owner opens the pull request.

## Outcome

Done. The metrics catalog, the structured query with its JSON schema, the compiler and a locked-down query runner are built and tested. Every metric compiles and runs on a synthetic DuckDB fixture and returns the hand-added value. Out-of-catalog input is refused with typed errors. Filter values are always bound parameters. No network call was made; the synthetic fixture is the only data.

Tests: 140 in `tests/semantic/` (the brief asks for at least 30); 145 in the whole suite.

## Verification

`uv run ruff check . && uv run ruff format --check . && uv run pytest -q && python3 scripts/commit_hygiene.py`, last lines, run on the branch before this report was committed:

```text
All checks passed!
28 files already formatted
........................................................................ [ 49%]
........................................................................ [ 99%]
.                                                                        [100%]
145 passed in 1.08s
{
  "commits": 7,
  "findings": [],
  "passed": true
}
```

## Done means, item by item

| Brief item | Where it is proven |
| --- | --- |
| Catalog defines the five metrics with the ADR-0002 rules, allowed dimensions and a description each | `catalog/metrics.yaml`; `tests/semantic/test_catalog.py` (five metrics, sign net, active-in-period count, parent-level recipient, competed share, fiscal months) |
| JSON schema exported by `query.py`; a validation error names the field | `QUERY_JSON_SCHEMA` and `query_json_schema()`; `test_query.py`, 19 cases each checking `error.field`, for example `filters[0].value[1]` |
| `compile(query)` returns SQL text and a parameter list; no filter value in the SQL, quote and semicolon included | `test_compiler.py::test_no_filter_value_appears_in_the_sql_text`, run for all five metrics with `x'); DROP TABLE fct_contract_transaction; --`, `P'1`, `P;2` and `V'A`; the compiled SQL holds no quote, no semicolon and no `--` at all |
| Typed refusals, each tested | `UnknownMetricError`, `UnknownDimensionError`, `DimensionNotAllowedError`, `UnknownOperatorError`, `FilterFieldNotDimensionError`, `MalformedTimeRangeError` (10 shapes), plus `UnknownGrainError` and `InvalidFilterValueError`; `test_compiler.py`, `test_query.py` |
| Every metric runs on the fixture with a hand-computed value, a net-negative award and a fiscal-month grouping | `test_metrics.py` (33 tests); award W03 nets 200.00 - 450.00 = -250.00; all five metrics are grouped by fiscal month |
| Read-only, `enable_external_access=false`, `lock_configuration=true`; a file-reading table function is refused | `test_runner.py`: settings read back; `read_text`, `read_csv`, `read_parquet`, `read_blob` and `glob` refused on three files; the lock cannot be undone; writes refused |
| At least 30 tests | 140 |

## What changed

All inside the brief's owned paths.

- `catalog/metrics.yaml`: grains (`total`, `fiscal_year`, `fiscal_quarter`, `fiscal_month`), eight dimensions, four operators, five metrics.
- `src/agentic_bi/semantic/`: `contract.py` (the contract's tables, columns and joins as data), `catalog.py` (strict loader), `query.py` (schema and validator), `compiler.py`, `runner.py` (query connection, row limit, timeout), `errors.py`.
- `tests/semantic/`: the fixture (`conftest.py`, 16 synthetic transactions listed in its docstring) and seven test files.
- `docs/catalog.md`: a plain-language guide. A test keeps its names and its table of allowed dimensions equal to the YAML.

## One compiled example per metric

Each runs on the synthetic fixture, fiscal year 2025 (`time_range` 2024-10-01 to 2025-09-30).

### obligations, by fiscal month

```json
{"metric": "obligations", "grain": "fiscal_month", "time_range": {"start": "2024-10-01", "end": "2025-09-30"}}
```

```sql
SELECT
    "d"."fiscal_year" AS "fiscal_year",
    "d"."fiscal_month" AS "fiscal_month",
    sum("f"."obligation_amount") AS "obligations"
FROM "fct_contract_transaction" AS "f"
LEFT JOIN "dim_date" AS "d" ON "d"."date" = "f"."action_date"
WHERE "f"."action_date" BETWEEN ? AND ?
GROUP BY "d"."fiscal_year", "d"."fiscal_month"
ORDER BY "fiscal_year" ASC NULLS LAST, "fiscal_month" ASC NULLS LAST
```

Parameters: `[2024-10-01, 2025-09-30]`. Result: months 1 to 12 give 1300.00, 800.00, 200.00, 1200.00, -450.00, 1100.00, 1000.00, 900.00, 800.00, 700.00, 600.00, 900.00 (total 9050.00). Month 5 is net negative.

### award_count, by award type

```json
{"metric": "award_count", "dimensions": ["award_type"], "time_range": {"start": "2024-10-01", "end": "2025-09-30"}}
```

```sql
SELECT
    "f"."award_type_code" AS "award_type",
    count(DISTINCT "f"."award_key") AS "award_count"
FROM "fct_contract_transaction" AS "f"
WHERE "f"."action_date" BETWEEN ? AND ?
GROUP BY "f"."award_type_code"
ORDER BY "award_type" ASC NULLS LAST
```

Parameters: `[2024-10-01, 2025-09-30]`. Result: A 2, B 2, C 6, D 3.

### avg_award_value, by fiscal quarter, award types A and B

```json
{"metric": "avg_award_value", "grain": "fiscal_quarter", "filters": [{"field": "award_type", "op": "in", "value": ["A", "B"]}], "time_range": {"start": "2024-10-01", "end": "2025-09-30"}}
```

```sql
SELECT
    "d"."fiscal_year" AS "fiscal_year",
    "d"."fiscal_quarter" AS "fiscal_quarter",
    sum("f"."obligation_amount") / nullif(count(DISTINCT "f"."award_key"), 0) AS "avg_award_value"
FROM "fct_contract_transaction" AS "f"
LEFT JOIN "dim_date" AS "d" ON "d"."date" = "f"."action_date"
WHERE "f"."action_date" BETWEEN ? AND ?
    AND "f"."award_type_code" IN (?, ?)
GROUP BY "d"."fiscal_year", "d"."fiscal_quarter"
ORDER BY "fiscal_year" ASC NULLS LAST, "fiscal_quarter" ASC NULLS LAST
```

Parameters: `[2024-10-01, 2025-09-30, "A", "B"]`. Result: Q1 200.0, Q2 375.0 ((1200 - 450) / 2), Q3 800.0, Q4 700.0.

### supplier_concentration_top10, by sub-agency, excluding one sub-agency

```json
{"metric": "supplier_concentration_top10", "dimensions": ["sub_agency"], "filters": [{"field": "sub_agency", "op": "ne", "value": "SA'3;"}], "time_range": {"start": "2024-10-01", "end": "2025-09-30"}}
```

```sql
WITH "_selection" AS (
    SELECT
        "f"."sub_agency_code" AS "sub_agency",
        "r"."reporting_uei" AS "_entity",
        "f"."obligation_amount" AS "_amount"
    FROM "fct_contract_transaction" AS "f"
    LEFT JOIN "dim_recipient" AS "r" ON "r"."recipient_uei" = "f"."recipient_uei"
    WHERE "f"."action_date" BETWEEN ? AND ?
        AND "f"."sub_agency_code" IS DISTINCT FROM ?
),
"_per_entity" AS (
    SELECT "sub_agency", "_entity", sum("_amount") AS "_entity_total"
    FROM "_selection"
    GROUP BY "sub_agency", "_entity"
),
"_ranked" AS (
    SELECT *, row_number() OVER (PARTITION BY "sub_agency" ORDER BY "_entity_total" DESC, "_entity" ASC NULLS LAST) AS "_entity_rank"
    FROM "_per_entity"
)
SELECT
    "sub_agency",
    coalesce(sum("_entity_total") FILTER (WHERE "_entity_rank" <= 10), 0) / nullif(sum("_entity_total"), 0) AS "supplier_concentration_top10"
FROM "_ranked"
GROUP BY "sub_agency"
ORDER BY "sub_agency" ASC NULLS LAST
```

Parameters: `[2024-10-01, 2025-09-30, "SA'3;"]`. Result: SA1 1.0, SA2 1.0 (fewer than ten recipients each). Over the whole fiscal year the test gets 9000 / 9050: the parent roll-up puts SYNP01 (1800.00) first; without it the top ten would hold 8700.

### competition_rate, by fiscal quarter, outside Virginia and Maryland

```json
{"metric": "competition_rate", "grain": "fiscal_quarter", "filters": [{"field": "pop_state", "op": "not_in", "value": ["VA", "MD"]}], "time_range": {"start": "2024-10-01", "end": "2025-09-30"}}
```

```sql
SELECT
    "d"."fiscal_year" AS "fiscal_year",
    "d"."fiscal_quarter" AS "fiscal_quarter",
    coalesce(sum("f"."obligation_amount") FILTER (WHERE "c"."is_competed" IS TRUE), 0) / nullif(sum("f"."obligation_amount"), 0) AS "competition_rate"
FROM "fct_contract_transaction" AS "f"
LEFT JOIN "dim_date" AS "d" ON "d"."date" = "f"."action_date"
LEFT JOIN "dim_competition" AS "c" ON "c"."competition_code" = "f"."competition_code"
WHERE "f"."action_date" BETWEEN ? AND ?
    AND ("f"."pop_state_code" IS NULL OR "f"."pop_state_code" NOT IN (?, ?))
GROUP BY "d"."fiscal_year", "d"."fiscal_quarter"
ORDER BY "fiscal_year" ASC NULLS LAST, "fiscal_quarter" ASC NULLS LAST
```

Parameters: `[2024-10-01, 2025-09-30, "VA", "MD"]`. Result: Q1 0.4 (200 of 500), Q2 1.0, Q3 1.0, Q4 1.0. Work performed abroad (null state) is kept by `not_in`.

## Choices made inside the brief

The owner may want to rule on these; each is easy to change.

1. **`grain` is the time breakdown, `dimensions` are the other fields.** Fiscal months and quarters always come with their fiscal year. Dates are filtered only by `time_range`, which is inclusive on both ends and optional (null means every date held).
2. **Allowed dimensions apply to filters too.** Concentration cannot use `recipient`; the competition rate cannot use `competition` or `competed`. The loader refuses a catalog that allows a metric to slice by its own entity or condition.
3. **Null handling.** Dimensions join with LEFT joins, so no fact row drops out of a total. `ne` and `not_in` keep rows whose field is null (for example work abroad). A transaction with no competition code counts as not competed.
4. **Closed and open values.** `award_type` accepts only A to D; other dimensions accept any text. All codes are text, so a NAICS code sent as a number is refused.
5. **Result types.** `obligations` is an exact DECIMAL; `avg_award_value` and the two shares are DOUBLE. A zero denominator gives null.
6. **Row limit and timeout (ADR-0002 point 6).** 10,000 rows by default, and more is an error, not a silent cut. 30 seconds by default, enforced by interrupting the connection.
7. **Operators and refusal codes** are listed in `docs/catalog.md`.

## Finding: DuckDB keeps the database's own files reachable

With `enable_external_access=false`, DuckDB 1.5.6 still lists the open database file, its `.wal` files and its temp directory as allowed paths. Two effects, both checked in a scratch probe outside the repository:

- file functions can read the database file itself (no new data: the connection can read it anyway);
- `COPY (SELECT 1) TO '<database>.wal'` succeeds on the read-only connection and creates that file. Setting `temp_directory` to empty does not change this.

No structured query can reach either: the compiler writes only SELECT statements. As a second line, the runner now parses the SQL with DuckDB and runs only a single SELECT statement (`test_runner_runs_only_a_single_select`). The file-access test uses files other than the database's own. Worth remembering for S4: never hand this connection raw SQL.

## Open questions

1. **Labels.** Dimensions return codes (sub-agency code, reporting UEI), not names. S4's schema tool or a label column could add names; not built here.
2. **Ordering and top-N.** The ADR's query shape has no `order_by` or `limit`, so "top five recipients" comes back as the full grouped list, under the row limit. Adding them would be an ADR-0002 change.
3. **Unknown recipients.** Transactions with no recipient UEI would form one "unknown" entity in the concentration ranking. The contract audit covers a UEI missing from `dim_recipient`, not a null UEI; S1 data will show whether nulls occur.
4. **Award type descriptions.** The catalog says A is a BPA call, B a purchase order, C a delivery order, D a definitive contract. That follows the USAspending code list as known to this session, not checked against a source today (no network beyond what the brief allows). S1 could confirm it from the data dictionary.
5. **Golden comparisons in S3b.** Shares and averages are DOUBLE; S3b needs a rounding or tolerance rule for `derived` golden values.
6. **Competition seed.** The fixture uses made-up codes (FC, FS, NC). The cited mapping arrives with S2.

## Blocked

Nothing.
