# agentic-bi

An analyst agent for US federal contract spending that never writes SQL: it emits a structured query, a compiler turns it into parameterised SQL over a catalog of defined measures, and every answer is checked against the publisher's own totals.

Status: under construction, see [docs/PLAN.md](docs/PLAN.md). Working rules: [AGENTS.md](AGENTS.md).

## Data notice

Data from [USAspending.gov](https://www.usaspending.gov), US Government work in the public domain. Slice and capture rules: [ADR-0001](docs/decisions/0001-dataset-usaspending-epa-fy2025.md). Personal contact data and officer compensation are excluded by an explicit column allow-list.
