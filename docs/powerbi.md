# Power BI semantic model

Status: built in S3c (2026-11-02) for [ADR-0004](decisions/0004-power-bi-semantic-model-from-the-catalog.md). The code is in `src/agentic_bi/powerbi/`; the project is in `powerbi/`; the data is in `data/model/`. The Desktop check at the end of this page is the owner's step and closes plan row S3c.

## What it is

A Power BI Desktop project (PBIP) whose semantic model is written by code from the same sources the compiler reads:

| Source | What the model takes from it |
| --- | --- |
| [docs/contracts/star-schema.md](contracts/star-schema.md) | one table per contract table, with the contract's column names, order and types, and the fact's column meanings as descriptions; the many-to-one joins from the fact |
| [catalog/metrics.yaml](../catalog/metrics.yaml) | one measure per metric, named after it, with its description and a format for its unit |
| [catalog/dax.yaml](../catalog/dax.yaml) | each measure's DAX, with a comment on how it follows the metric's SQL rule |

So the agent and a Power BI report answer from one set of measure definitions. The generated files are never edited by hand: a test regenerates them and fails on any difference.

## The data: `data/model/`

```sh
uv run python -m agentic_bi.powerbi.export --out data/model
```

The command builds the warehouse with `agentic_bi.warehouse.build` into `data/tmp/powerbi/warehouse.duckdb` (gitignored), then writes one Parquet file per contract table, named after the table, with the contract's columns and types and the rows sorted by the table's key. It reads the fact file back and stops with an error unless its row count and obligation sum equal `data/snapshot/manifest.json` (12,687 rows, 1,697,318,332.65 USD on the 2026-11-01 snapshot). `data/model/manifest.json` records each file's rows, bytes and SHA-256.

The eight files take 428,153 bytes together (0.43 MB), under the 10 MB limit of ADR-0004, so they are committed. The same database gives the same bytes on every run (one writer thread, Snappy compression).

## The project: `powerbi/`

```sh
uv run python -m agentic_bi.powerbi.generate --out powerbi
```

| Path under `powerbi/` | Content |
| --- | --- |
| `AgenticBI.pbip` | the file Power BI Desktop opens; it points at the report folder |
| `AgenticBI.Report/definition.pbir` | the report's pointer to the semantic model, by relative path |
| `AgenticBI.SemanticModel/definition.pbism` | declares a model held as TMDL (version 4.0) |
| `AgenticBI.SemanticModel/definition/database.tmdl` | compatibility level 1567 |
| `AgenticBI.SemanticModel/definition/model.tmdl` | culture en-US, implicit measures discouraged, auto date/time off |
| `AgenticBI.SemanticModel/definition/expressions.tmdl` | the `DataFolder` text parameter |
| `AgenticBI.SemanticModel/definition/relationships.tmdl` | the seven many-to-one relationships from the fact |
| `AgenticBI.SemanticModel/definition/tables/*.tmdl` | the eight contract tables; the five measures sit on the fact |

What the model holds:

- **Tables.** Each table has one Power Query partition in import mode that reads `DataFolder & "\<table>.parquet"`, keeps the contract's columns and sets their types: VARCHAR is text, DATE a date, INTEGER a whole number, BOOLEAN true/false, and `obligation_amount` (DECIMAL(18,2)) a fixed decimal number, so sums stay exact to the cent.
- **Relationships.** The fact joins each dimension many-to-one on the contract's keys, filtering from the dimension to the fact. A transaction with no competition code joins the blank row of `dim_competition`.
- **Date table.** `dim_date` is marked as the date table, on its `date` column; auto date/time is off.
- **Hidden columns.** The fact's join keys and `obligation_amount` are hidden: a report filters through the dimensions and aggregates through the measures.
- **Measures.** `obligations`, `award_count`, `avg_award_value`, `supplier_concentration_top10` and `competition_rate`, each with the catalog's description. US dollars show two decimal places, award counts none, shares six.

The generator owns `AgenticBI.pbip`, `definition.pbism`, everything under `AgenticBI.SemanticModel/definition/` (it removes any other file there) and `definition.pbir`. Report pages, visuals and any file whose schema Microsoft does not publish (`report.json`, `diagramLayout.json`) are written by Power BI Desktop only. Files are UTF-8 without a byte-order mark, with CRLF line endings; `.gitattributes` keeps them so on every system, and `.gitignore` leaves out `.pbi/cache.abf` and `.pbi/localSettings.json`. The longest path is under 80 characters, far below the Windows limit of 260.

## How each measure follows its SQL rule

The DAX is in [catalog/dax.yaml](../catalog/dax.yaml), with a longer comment per metric. Filter context plays the part of the compiler's `WHERE` and `GROUP BY`. Where the compiler returns null, the measures return BLANK.

| Metric | DAX, in short | The rule it keeps |
| --- | --- | --- |
| `obligations` | `SUM` of `obligation_amount` | Sign: net, de-obligations added as negative amounts |
| `award_count` | `DISTINCTCOUNTNOBLANK` of `award_key` | Active in period: an award counts when one of its transactions is in the filter context, whatever its amount |
| `avg_award_value` | `DIVIDE ( [obligations], [award_count] )` | Each side keeps its own rule; BLANK when the count is 0 |
| `supplier_concentration_top10` | `TOPN` of 10 over the fact's `reporting_uei`, by net obligations then by UEI, divided by the sum over all | Reporting parent; top-10 share of net obligations, 1 with ten or fewer recipients |
| `competition_rate` | net obligations where `dim_competition[is_competed]` is TRUE, kept with `KEEPFILTERS`, divided by net obligations | Competed share; no competition code counts as not competed |

One difference in form, not in value: on an empty selection `award_count` is BLANK in Power BI, where the compiler's total query returns 0. Grouped results agree, since neither shows an empty group.

Two differences in what can be asked:

- **Refused filters.** The compiler refuses some groupings, such as `supplier_concentration_top10` by recipient or `competition_rate` by `competed`, because the answer is always 1, or 0 or 1. Power BI has no such refusal: a report can slice these measures that way and shows those trivial values.
- **Text comparison.** Power BI compares text without regard to case, and joins keys without trailing spaces; DuckDB does neither. Keys that differ only that way would merge in Power BI. None does in the committed data, and a test checks every text key of `data/model/`.

## Open the project in Power BI Desktop

Power BI Desktop runs on Windows only. These steps are the owner's.

1. Clone the repository on the Windows machine. The Parquet files are committed, so no export is needed.
2. Open `powerbi\AgenticBI.pbip` in Power BI Desktop.
3. Set the data folder: **Transform data**, then **Edit parameters**. Set `DataFolder` to the full path of the clone's `data\model` folder, without a trailing backslash, for example `C:\Users\<you>\agentic-bi\data\model`. The starting value `C:\agentic-bi\data\model` only fits a clone at `C:\agentic-bi`.
4. Apply the change and **Refresh**. If Desktop asks about privacy levels for the local files, any level works: every query reads the same folder.
5. Check the model view: eight tables, seven relationships from `fct_contract_transaction`, `dim_date` marked as the date table (**Table tools**, **Mark as date table**).
6. Fill the check table below.

If Desktop refuses to open the project because the report folder holds only `definition.pbir` (ADR-0004, consequences):

1. Close Desktop. Open a blank report and save it as a Power BI project (`.pbip`) named `AgenticBI` in a scratch folder outside the clone, for example `C:\scratch`. Close Desktop.
2. Copy the contents of `C:\scratch\AgenticBI.Report` into the clone's `powerbi\AgenticBI.Report`. Copy nothing else: the scratch `AgenticBI.SemanticModel` is an empty model and may be held as `model.bim`, which must not land next to the generated TMDL.
3. Run `uv run python -m agentic_bi.powerbi.generate --out powerbi` again. It rewrites `definition.pbir` and keeps the report files Desktop saved.
4. Open `powerbi\AgenticBI.pbip` again and continue at step 3 above.

Record in the pull request that this fallback was needed; the generator then stops writing the report folder in a later session.

### What Desktop changes, and what to commit

Saving in Desktop rewrites the TMDL files: it may add lineage tags, annotations, `ref` lines, a `cultures` folder and `diagramLayout.json`. The CI test then fails until the generated files are restored. Before committing:

```sh
uv run python -m agentic_bi.powerbi.generate --out powerbi
```

Then commit only report work (pages, visuals) under `AgenticBI.Report/`. A change to a table, a column or a measure goes into the contract, `catalog/metrics.yaml` or `catalog/dax.yaml`, never into the TMDL.

## Desktop check of the golden values

Each value must equal [docs/golden/golden.json](golden/golden.json) to the cent, or to the sixth decimal place for a share (ADR-0004 point 4). Use a table visual with the measure and the filter shown: a card shows large amounts in display units such as "1.70bn" unless its display units are set to None. The golden value column is copied from `golden.json`, and a test keeps it so.

| # | Golden id | Tag | Measure | Filter in Desktop | Golden value | Desktop value |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `obligations.total` | owner_api | `obligations` | none | 1697318332.65 | |
| 2 | `obligations.fiscal_month.04` | owner_api | `obligations` | `dim_date[fiscal_year]` = 2025 and `dim_date[fiscal_month]` = 4 | 311975420.85 | |
| 3 | `obligations.recipient.DBD6KDKXWN65` | owner_api | `obligations` | `dim_recipient[recipient_uei]` = DBD6KDKXWN65 | 141735682.10 | |
| 4 | `awards.average_net_obligation` | derived | `avg_award_value` | none | 282134.03 | |
| 5 | `recipients.top10_reporting_share` | derived | `supplier_concentration_top10` | none | 0.449020 | |

Notes for the check:

- Row 3 filters the recipient's own UEI through `dim_recipient`, as the publisher ranks recipients. The catalog's `recipient` dimension is the reporting parent (`dim_reporting_recipient`), which can hold a different amount.
- Row 4 is shown with two decimal places; the full value is 1,697,318,332.65 / 6,016 awards.
- A test computes the same five values from the committed Parquet files, rows 1, 2, 4 and 5 through the compiler and row 3 by a plain sum over `recipient_uei` (the catalog has no dimension for a recipient's own UEI). A difference in Desktop therefore points at the DAX or the model, not the data.
