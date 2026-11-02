# Report: s3c-powerbi-semantic-model

Date: 2026-11-02. Branch: `s3c-powerbi-semantic-model`, from `main` at `9f55f6a`. Not merged; the owner opens the pull request.

## Outcome

Done, except the owner's Desktop check, which the brief leaves to the owner. The contract tables are exported to Parquet (0.43 MB, committed). A generator writes a Power BI Desktop project in TMDL from the star-schema contract, `catalog/metrics.yaml` and the new `catalog/dax.yaml`. Tests regenerate it and fail on any difference, check the files' encoding and line endings, and check the TMDL against the documented grammar with an independent checker. No Microsoft tool and no new dependency was used.

Microsoft's documentation was not fetched: the session's network allows package installs and the USAspending hosts only (AGENTS.md). Following the brief's stop rule, the generator stays conservative and every TMDL or PBIP detail written from knowledge rather than from the documentation is listed below for the owner to check in Desktop.

Tests: 227 in `tests/powerbi/`; 475 in the whole suite. The 108 warnings are pydantic deprecation notices raised inside SQLMesh when the warehouse is built; the warehouse tests show the same.

## Verification

`uv run ruff check . && uv run ruff format --check . && uv run pytest -q && python3 scripts/commit_hygiene.py`, last lines, run on the branch before this report was committed:

```text
All checks passed!
70 files already formatted
........................................................................ [ 15%]
........................................................................ [ 30%]
........................................................................ [ 45%]
........................................................................ [ 60%]
........................................................................ [ 75%]
........................................................................ [ 90%]
...........................................                              [100%]
=============================== warnings summary ===============================
475 passed, 108 warnings in 12.04s
{
  "commits": 29,
  "findings": [],
  "passed": true
}
```

## Done means, item by item

| Brief item | Where it is proven |
| --- | --- |
| `export --out data/model` builds the warehouse into `data/tmp/`, writes one Parquet file per contract table; fact rows and obligation sum equal the snapshot manifest; total size reported; committed only under 10 MB | `src/agentic_bi/powerbi/export.py` builds into `data/tmp/powerbi/warehouse.duckdb` and stops with an error on a mismatch. `test_powerbi_export.py`: one file per table, contract columns and types per file, 12,687 rows and 1,697,318,332.65, a mismatch refused, byte-identical reruns, committed files equal a fresh export, committed size under 10,000,000 bytes. Total: 428,153 bytes, so the files are committed |
| `catalog/dax.yaml`: one DAX expression per metric, keyed by name, each with a comment on its SQL rule; a test fails on a missing metric or an extra key | `catalog/dax.yaml`, loaded by `src/agentic_bi/powerbi/dax.py`. `test_powerbi_dax.py`: a missing metric, an extra metric, an unknown entry key, an unknown top-level key, a wrong version, an empty expression are each refused; each expression is tied to its catalog parameters (column, n, entity, condition); each comment names its rule |
| `generate --out powerbi` writes `AgenticBI.SemanticModel` in TMDL: tables with contract names and types, `DataFolder` used by every partition, the many-to-one relationships, `dim_date` as date table, one measure per metric with the catalog description | `src/agentic_bi/powerbi/generate.py`. `test_powerbi_generate.py`: eight tables with contract columns and data types, each partition reads `DataFolder & "\<table>.parquet"`, seven relationships equal the contract joins, only `dim_date` has `dataCategory: Time` with `isKey` on `date`, five measures with the exact catalog description and the exact DAX of `dax.yaml` |
| A test regenerates into a temporary folder and fails when any committed file under `powerbi/` differs | `test_regenerating_into_a_temporary_folder_matches_the_committed_project` and `test_the_committed_definition_folder_holds_only_generated_files` |
| UTF-8 without BOM, CRLF, kept by `.gitattributes`; short paths | `test_files_are_utf8_without_bom_with_crlf_line_endings` (generated and committed bytes), `test_gitattributes_keeps_crlf_for_every_generated_file` (`git check-attr`), `test_paths_stay_short` (longest is 79 characters). Fresh clones with `core.autocrlf` unset, `input` and `true` all checked out byte-identical files |
| TMDL checked against the documented grammar: indentation, object keywords, quoting of names with spaces, every measure, column and relationship refers to an existing object | `tests/powerbi/tmdl_grammar.py` and `test_tmdl_grammar.py` (67 tests), written by a separate subagent from the grammar and the output only, without reading the generator. Mutation tests: a space in an unquoted name, space indentation, wrong depth, unknown property or keyword, a dangling relationship, DAX or M reference, a correctly quoted renamed column that passes |
| `docs/powerbi.md`: open in Desktop, set `DataFolder`, check table of five golden values (one derived, one by fiscal month, one by recipient), empty Desktop column | `docs/powerbi.md`. `test_powerbi_docs.py`: five rows, values equal `golden.json`, two derived, one fiscal month, one recipient, the Desktop column empty; the five values are computed again from the committed Parquet files through the compiler |

## Generated file list

`powerbi/`, written by `uv run python -m agentic_bi.powerbi.generate --out powerbi`:

| File | Bytes |
| --- | --- |
| `AgenticBI.pbip` | 290 |
| `AgenticBI.Report/definition.pbir` | 245 |
| `AgenticBI.SemanticModel/definition.pbism` | 172 |
| `AgenticBI.SemanticModel/definition/database.tmdl` | 37 |
| `AgenticBI.SemanticModel/definition/model.tmdl` | 181 |
| `AgenticBI.SemanticModel/definition/expressions.tmdl` | 357 |
| `AgenticBI.SemanticModel/definition/relationships.tmdl` | 1,001 |
| `AgenticBI.SemanticModel/definition/tables/fct_contract_transaction.tmdl` | 7,109 |
| `AgenticBI.SemanticModel/definition/tables/dim_date.tmdl` | 1,879 |
| `AgenticBI.SemanticModel/definition/tables/dim_recipient.tmdl` | 881 |
| `AgenticBI.SemanticModel/definition/tables/dim_reporting_recipient.tmdl` | 911 |
| `AgenticBI.SemanticModel/definition/tables/dim_sub_agency.tmdl` | 896 |
| `AgenticBI.SemanticModel/definition/tables/dim_psc.tmdl` | 847 |
| `AgenticBI.SemanticModel/definition/tables/dim_naics.tmdl` | 869 |
| `AgenticBI.SemanticModel/definition/tables/dim_competition.tmdl` | 1,144 |

`data/model/`, written by `uv run python -m agentic_bi.powerbi.export --out data/model`:

| File | Rows | Bytes |
| --- | --- | --- |
| `fct_contract_transaction.parquet` | 12,687 | 338,794 |
| `dim_date.parquet` | 365 | 2,830 |
| `dim_recipient.parquet` | 1,165 | 34,892 |
| `dim_reporting_recipient.parquet` | 1,132 | 34,196 |
| `dim_sub_agency.parquet` | 1 | 489 |
| `dim_psc.parquet` | 342 | 9,753 |
| `dim_naics.parquet` | 226 | 6,528 |
| `dim_competition.parquet` | 6 | 671 |
| total | | 428,153 (0.43 MB) |

plus `data/model/manifest.json` (rows, bytes and SHA-256 per file, the fact's checked totals).

## Each metric's DAX next to its SQL rule

The SQL is what `agentic_bi.semantic.compile` writes for a `total` query; filter context in Power BI plays the part of its `WHERE` and `GROUP BY`. The full comment per metric is in `catalog/dax.yaml`.

### `obligations`

Rule: Sign: net, de-obligations negative.

SQL (`total`):

```sql
SELECT
    sum("f"."obligation_amount") AS "obligations"
FROM "fct_contract_transaction" AS "f"
```

DAX:

```dax
SUM ( fct_contract_transaction[obligation_amount] )
```

### `award_count`

Rule: Active in period.

SQL (`total`):

```sql
SELECT
    count(DISTINCT "f"."award_key") AS "award_count"
FROM "fct_contract_transaction" AS "f"
```

DAX:

```dax
DISTINCTCOUNTNOBLANK ( fct_contract_transaction[award_key] )
```

### `avg_award_value`

Rule: Ratio of the two, each with its own rule.

SQL (`total`):

```sql
SELECT
    sum("f"."obligation_amount") / nullif(count(DISTINCT "f"."award_key"), 0) AS "avg_award_value"
FROM "fct_contract_transaction" AS "f"
```

DAX:

```dax
DIVIDE ( [obligations], [award_count] )
```

### `supplier_concentration_top10`

Rule: Top-10 share at the reporting parent.

SQL (`total`):

```sql
WITH "_selection" AS (
    SELECT
        "f"."reporting_uei" AS "_entity",
        "f"."obligation_amount" AS "_amount"
    FROM "fct_contract_transaction" AS "f"
),
"_per_entity" AS (
    SELECT "_entity", sum("_amount") AS "_entity_total"
    FROM "_selection"
    GROUP BY "_entity"
),
"_ranked" AS (
    SELECT *, row_number() OVER (ORDER BY "_entity_total" DESC, "_entity" ASC NULLS LAST) AS "_entity_rank"
    FROM "_per_entity"
)
SELECT
    coalesce(sum("_entity_total") FILTER (WHERE "_entity_rank" <= 10), 0) / nullif(sum("_entity_total"), 0) AS "supplier_concentration_top10"
FROM "_ranked"
```

DAX:

```dax
VAR _per_recipient =
    ADDCOLUMNS (
        VALUES ( fct_contract_transaction[reporting_uei] ),
        "@obligations", [obligations]
    )
VAR _top_10 =
    TOPN (
        10,
        _per_recipient,
        [@obligations], DESC,
        fct_contract_transaction[reporting_uei], ASC
    )
VAR _top_10_obligations = SUMX ( _top_10, [@obligations] )
VAR _all_obligations = SUMX ( _per_recipient, [@obligations] )
RETURN
    DIVIDE ( COALESCE ( _top_10_obligations, 0 ), _all_obligations )
```

### `competition_rate`

Rule: Competed share; no code is not competed.

SQL (`total`):

```sql
SELECT
    coalesce(sum("f"."obligation_amount") FILTER (WHERE "c"."is_competed" IS TRUE), 0) / nullif(sum("f"."obligation_amount"), 0) AS "competition_rate"
FROM "fct_contract_transaction" AS "f"
LEFT JOIN "dim_competition" AS "c" ON "c"."competition_code" = "f"."competition_code"
```

DAX:

```dax
VAR _all_obligations = [obligations]
VAR _competed_obligations =
    CALCULATE (
        [obligations],
        KEEPFILTERS ( dim_competition[is_competed] = TRUE () )
    )
RETURN
    DIVIDE ( COALESCE ( _competed_obligations, 0 ), _all_obligations )
```

No metric needed a different rule in DAX, so no SQL rule was touched. One difference in form: on an empty selection `award_count` is BLANK in Power BI where the compiler's total query returns 0; grouped results agree.

## TMDL and PBIP details not verified against Microsoft's documentation

Written from knowledge of TMDL and of files Power BI Desktop writes; each needs the owner's Desktop check.

1. `database.tmdl` declares `database` with no name, `compatibilityLevel: 1567`.
2. `model.tmdl`: `defaultPowerBIDataSourceVersion: powerBI_V3`, `discourageImplicitMeasures` written bare for true, `sourceQueryCulture: en-US`, and the annotation `__PBI_TimeIntelligenceEnabled = 0` at the file's top level after the model, where Desktop writes it.
3. No `ref table` lines in `model.tmdl`. The table files are expected to load from `definition/tables/` without them; Desktop adds them on save (the grammar subagent raised this too).
4. Boolean properties written bare for true: `isHidden`, `isKey`.
5. The date table is marked by `dataCategory: Time` on `dim_date` plus `isKey` on `date` (a `dateTime` column).
6. Multi-line expressions start after `=` and are indented two levels deeper than the declaring line: three tabs for a measure, four for a partition's `source`.
7. The parameter: `expression DataFolder = "..." meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]` with `annotation PBI_ResultType = Text` and a `///` description.
8. Partitions: `partition <table> = m`, `mode: import`, `source =`; tables carry `annotation PBI_ResultType = Table`; date columns carry `annotation UnderlyingDateTimeDataType = Date`.
9. Relationship names and every `lineageTag` are UUIDs derived from object names (UUID version 5), so they are stable across runs. Cardinality and cross-filter direction are left at their defaults (many-to-one, single).
10. Format strings unquoted: `#,0.00`, `#,0`, `0.000000`, `0`, `yyyy-mm-dd`.
11. `definition.pbism`: schema `semanticModel/definitionProperties/1.0.0`, `"version": "4.0"`, `"settings": {}`. `definition.pbir`: schema `report/definitionProperties/2.0.0`, `"version": "4.0"`, `datasetReference.byPath.path` `../AgenticBI.SemanticModel`. `AgenticBI.pbip`: schema `pbip/pbipProperties/1.0.0`, one report artifact.
12. Power Query reads the DuckDB-written Parquet files (Snappy, `DECIMAL(18,2)` stored as INT64, `DATE` as INT32) with `Parquet.Document(File.Contents(...))`, and `Currency.Type` holds the amounts exactly.
13. The report folder holds only `definition.pbir`; no `report.json` and no PBIR `definition/` folder, since the brief forbids `report.json`. Desktop may refuse to open it; `docs/powerbi.md` gives the ADR-0004 fallback (save a blank report as PBIP in a scratch folder, copy its report folder in, regenerate).
14. No `.platform` files are written. They serve Fabric Git integration, which is out of scope; Desktop is expected to open a project without them.
15. The grammar checker's own unsure points: which names need quotes (it applies the stricter rule, plain identifiers only, with relationship UUIDs allowed bare), whether a blank line between `///` and its object is refused, the exact depth Desktop requires for expression bodies, and its lists of allowed values and children, all from knowledge.

## What the owner must check in Desktop

1. `powerbi\AgenticBI.pbip` opens; if not, the fallback in `docs/powerbi.md` and a note in the pull request.
2. `DataFolder` set to the clone's `data\model`, refresh without error, row counts as in `data/model/manifest.json`.
3. Model view: eight tables, seven relationships from the fact (many-to-one, single direction), `dim_date` marked as the date table, the fact's join keys and amount hidden.
4. Five measures with their descriptions and formats.
5. The five values of the check table in `docs/powerbi.md`, to the cent or to six decimal places, recorded in its Desktop column.
6. After any save in Desktop, run the generator again before committing (Desktop rewrites the TMDL; the CI test would fail).

## Review

Two subagents that did not write the generator took part. One wrote the TMDL grammar checker from the grammar and the generated files alone; it found nothing it believed invalid and raised the missing `ref table` lines (detail 3 above). One reviewed the whole diff against the brief's `done_means` and the Code Review Rules, from the diff and the brief. It confirmed the scope (every path owned, `.gitignore` two lines only, no dependency change), the five golden values, the DAX against the compiler's SQL under every filter the compiler allows, and found no injection, secret or shell use. Its findings and what became of them:

| Finding | Outcome |
| --- | --- |
| `generate.py` checked links on the output folder and `definition/` only; a linked `AgenticBI.SemanticModel` or `AgenticBI.Report`, or a linked file, could be written or emptied through | Fixed: any link between the output folder and a written or removed path is refused; three tests added |
| The export wrote into `data/model/` before the snapshot check, so a failed check left files that do not reconcile | Fixed: files are written beside the build and moved only after the check passes; a test proves `--out` stays untouched |
| A chain of stale folders under `definition/` was only partly removed | Fixed: bottom-up removal; a test added |
| A card visual shows "1.70bn", which cannot be compared to the cent | Fixed in `docs/powerbi.md`: use a table visual, or display units None |
| The doc said all five values are computed through the compiler; the recipient row is a plain sum | Fixed in `docs/powerbi.md` |
| The Desktop fallback could leave a `model.bim` next to the generated TMDL | Fixed: the fallback saves the blank report in a scratch folder and copies only its report folder |
| Power BI compares text without case and trims trailing spaces on keys; DuckDB does not | A test now checks every text key of `data/model/` stays distinct when folded; none merges. Noted in `docs/powerbi.md` |
| Power BI allows slicing a measure in ways the compiler refuses | Noted in `docs/powerbi.md`; not a value difference |
| The report file was missing at review time | This file |
| `database` written without a name; no `.platform` files; a report folder with only `definition.pbir` may be refused | Kept, listed among the unverified details above (1, 13, 14) |

## What changed

All inside the brief's owned paths.

- `src/agentic_bi/powerbi/`: `schema.py` (the contract with types, from the document, cross-checked with `agentic_bi.semantic.contract`), `export.py`, `dax.py`, `tmdl.py` (names, references, descriptions, expressions, CRLF bytes), `generate.py`.
- `catalog/dax.yaml`; `data/model/` (eight Parquet files and `manifest.json`); `powerbi/` (15 generated files).
- `tests/powerbi/`: export, schema, DAX, generator, docs, and the independent TMDL grammar checker.
- `.gitattributes` (new: CRLF for the project's text files, Parquet binary); `.gitignore` (the two `.pbi/` paths only); `docs/powerbi.md`.

## Open questions

- The README and `docs/PLAN.md` are outside the owned paths. ADR-0004 point 4 says the README states that the Desktop check is manual; that line is left for a session that owns the README.
- `catalog/metrics.yaml` describes the `recipient` dimension at the reporting level, while the golden recipient values are by the recipient's own UEI. The check table filters `dim_recipient` for that reason; the note under the table says so.
