# ADR-0002: Structured query and compiler, not free SQL

Status: Accepted, 2026-10-20 (owner decision 1). Recorded in the repository 2026-11-01. This is the project's thesis.

## Context

Text-to-SQL agents fail in ways that are hard to see: a plausible query with a wrong join or a wrong sign returns a confident wrong number. A business-intelligence answer is only as good as the definition of its measure.

## Decision

1. **The agent never writes SQL.** It emits a structured query: `{metric, dimensions, filters, grain, time_range}`, validated against a JSON schema.
2. **A compiler renders SQL** from the metrics catalog (YAML). Measures, their sign rules and their allowed dimensions live in the catalog, not in prompts.
3. **Refusal is structural.** A metric, dimension, operator or filter value outside the catalog is a compiler error. The agent reports the refusal; it never improvises a substitute.
4. **Parameter binding.** The compiler binds every filter value as a DuckDB parameter. No value is ever formatted into SQL text.
5. **`run_query` accepts only a validated structured query** and runs the compiler's output. It has no raw-SQL input.
6. **Query connection.** Read-only, `enable_external_access=false`, `lock_configuration=true`, a row limit and a timeout.
7. **The schema tool** exposes catalog dimensions and their allowed values only, not the physical tables.
8. **The verifier** (a cheaper model, S5) checks question to structured query against the catalog. It checks intent, not SQL.
9. **Tool results are data.** Text returned by a tool is never treated as an instruction. The analyst agent runs with no built-in tools, only the project's four: `catalog`, `schema`, `run_query`, `explain_measure`.

## Metric rules the catalog must fix

| Rule | Default |
| --- | --- |
| Sign | Obligations are net: `sum(federal_action_obligation)`, de-obligations included as negative values |
| Award count | Distinct awards with at least one transaction in the period ("active in period"); "new in period" is a separate metric if ever added |
| Recipient level | Parent UEI, falling back to the recipient's own UEI when no parent is recorded; stated in the metric's description |
| Concentration | Share of net obligations held by the top 10 recipients at the parent level |
| Competition rate | Share of net obligations on transactions whose competition code is marked competed in `dim_competition` |
| Months | Fiscal months (October is month 1) |

Golden cases include awards with net-negative obligations.

## Consequences

- The compiler is a small project of its own. If S3 overflows one session, it splits into S3a (catalog and compiler) and S3b (golden reproduction).
- The eval measures intent accuracy and refusal behaviour, not SQL similarity.
