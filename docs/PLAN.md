# Plan

Status: working plan, 2026-11-01. One increment per session, one pull request per increment. Nothing past S6 starts before S6 closes.

## Architecture

```text
USAspending API -> pinned Parquet snapshot (sha256) -> SQLMesh models -> DuckDB
publisher aggregate endpoints (same day) -> docs/golden/
                                  |
     metrics catalog (YAML) ------+--> compiler (Python) -> parameterised SQL
                                  |
  analyst agent (Agent SDK), tools: catalog, schema, run_query, explain_measure
                                  |
       verifier (cheaper model): question <-> structured query <-> catalog
                                  |
        eval harness: golden set of 30 to 50 -> pytest -> CI accuracy report
```

## Sessions

| S | Deliverable | Done means |
| --- | --- | --- |
| S0 | Repository, working rules, CI, ADR-0001 to ADR-0003, the star-schema contract, briefs for S1 and S3a | CI green on `main`; this plan and the briefs committed |
| S1 | Fetch script (snapshot plus manifest) and golden script (30 to 50 values, tagged) | Row count and contract obligations equal the publisher's totals of the same day, to the cent; both scripts rerun cleanly; CI uses recorded fixtures, no network |
| S2 | SQLMesh project: the tables of `docs/contracts/star-schema.md`, audits, the competition seed. Time-boxed to one session; otherwise dbt-duckdb, recorded in an ADR | `sqlmesh plan` clean; row count and three golden totals match S1 |
| S3a | Metrics catalog (obligations, award count, average award value, supplier concentration, competition rate) and compiler, with unit tests on a synthetic fixture | Every metric compiles; out-of-catalog input is refused; filter values are bound parameters |
| S3b | Golden reproduction through the compiled SQL on the S2 models | Five golden values reproduced, at least one `derived` |
| S4 | Analyst agent with the four tools; every answer carries the structured query, the SQL and the numbers | Ten manual questions answered with traceable SQL; an out-of-catalog question refused |
| S5a | Verifier | Intent checks on the S4 questions |
| S5b | Eval harness and CI | Compiler golden tests on every PR with no key; the LLM eval on manual or scheduled runs only, with a pinned model and a spend cap |
| S6 | README, phase close | Status recorded; the repository presentable |

## Phase two (not before S6 closes)

Scheduled insights loop (USAspending updates monthly), MCP server, a Power BI path, model lint, a second dataset.

## Risks

- Scope creep: nothing past S6 before S6 closes.
- The compiler can overflow S3: split as above.
- Late corrections move FY2025 figures: capture snapshot and golden values on the same day, never mix days.
- Golden values are not independent of the records: say so in the README.
- USAspending field renames: pin the field list, fail loudly.
- Public repository and personal data: the column allow-list of ADR-0001 point 6, checked by a test.
