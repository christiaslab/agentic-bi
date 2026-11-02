# Report: s4-analyst-agent

Date: 2026-11-02. Branch: `s4-analyst-agent`, from `main` at `9f55f6a`. Not merged; the owner opens the pull request. Brief: [tasks/s4-analyst-agent.json](../s4-analyst-agent.json). Plan row S4, serves ADR-0002.

## Outcome

Done, not blocked. The analyst agent of ADR-0002 is built and proven offline with no API key:

- a model answers only through the four project tools (`catalog`, `schema`, `run_query`, `explain_measure`), never writes SQL, and returns a typed answer: the structured queries it ran with their SQL, parameters and rows, and a short text whose every figure appears in those rows, or a refusal with the catalog's code;
- the structured query gained `order_by` and `limit` (an Amendment to ADR-0002), with the limit bound as a parameter;
- sixteen hand-written replays (twelve answered, four refused) drive the real loop and tools on the warehouse built from the committed snapshot; every golden value they touch is reproduced, 39 golden values and one ratio of golden values;
- the runtime is the Anthropic Messages API (`anthropic==2.3.0`) with the project's own tool runner (ADR-0005); the default model is `claude-sonnet-5-5`, a setting;
- `uv run python -m agentic_bi.agent ask` and `session` run live with the owner's key and stop with a clear message without it.

The live run of the questions is the owner's step and closes the row (commands below).

## Verification

`uv run ruff check . && uv run ruff format --check . && uv run pytest -q && python3 scripts/commit_hygiene.py`, last lines, run on the branch at `498477c`, before this report was committed:

```text
All checks passed!
87 files already formatted
........................................................................ [ 97%]
.........................................                                [100%]
=============================== warnings summary ===============================
.venv/lib/python3.12/site-packages/pydantic/_internal/_generate_schema.py:300: 108 warnings
  [...] PydanticDeprecatedSince20: `json_encoders` is deprecated. [...]
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
1985 passed, 108 warnings in 15.45s
{
  "commits": 45,
  "findings": [],
  "passed": true
}
```

The 108 warnings come from inside SQLMesh, as on `main`. No test is skipped or marked xfail.

Tests: 1985 in the whole suite (248 on `main`): 1688 in `tests/agent/` (16 files) and 189 in `tests/semantic/` (49 of them new, in `test_ordering.py`). The suite needs no key and makes no network call.

## Library and version

- `anthropic==2.3.0` from PyPI (<https://pypi.org/project/anthropic/2.3.0/>, released 2026-10-23), the one new direct dependency, in `[project] dependencies`. It sends API version `2026-10-21`.
- `uv.lock` was regenerated with uv 0.12.3 (the CI pin; the container's own uv is 0.11.32). It gains 11 packages, all from PyPI: anthropic 2.3.0, anyio 4.16.0, docstring-parser 0.18.0, h11 0.16.0, httpcore2 2.16.0, httpx2 2.16.0, httpx2-jsfetch 1.0 (emscripten only, not installed), jiter 0.18.0, opentelemetry-api 1.45.2, sniffio 1.3.1, truststore 0.10.5. `uv lock --check` is clean.
- Default model `claude-sonnet-5-5`, read from `AgentSettings.model`, overridden by `AGENTIC_BI_MODEL` or `--model`. No prompt names a model (tested).
- The library installed and is tested without a key (a stand-in client checks the request; replays drive the loop), so the brief's fallback to the other runtime was not needed. The Claude Agent SDK was compared on design and not installed.

## Done means, item by item

| Brief item | Where it is proven |
| --- | --- |
| ADR-0005: runtime, why, exact pin with PyPI source, default model, model id a setting | [docs/decisions/0005-analyst-agent-runtime.md](../../docs/decisions/0005-analyst-agent-runtime.md); `test_agent_docs.py` checks the pin and the model name; `test_agent_prompt.py` checks the prompt names no model; `test_agent_model.py` checks the request takes the model from the settings |
| ADR-0002 Amendment; schema, parser and compiler support `order_by` and `limit`; limit bound; tests for top 5 recipients, a bad `order_by` field, limit out of range | Amendment section of [ADR-0002](../../docs/decisions/0002-structured-query-and-compiler.md); `tests/semantic/test_ordering.py` (top 5 recipients on the fixture and as exact SQL with the limit as the last parameter, 14 bad `order_by` cases, 9 bad `limit` cases, hand-built queries, the docs example) |
| The four tools as plain functions with tests | `src/agentic_bi/agent/tools.py`; `tests/agent/test_agent_tools.py`, `test_agent_envelope.py` |
| A typed answer: question, queries, SQL, parameters, rows, a text whose every figure is in the rows, or a refusal naming the catalog reason | `answer.py`, `final.py`, `figures.py`; `test_agent_loop.py`, `test_agent_figures.py`, `test_agent_final.py`, `test_agent_names.py` |
| Tool results reach the model as data inside a clear delimiter; a recipient name holding an instruction changes nothing | `envelope.py`; `tests/agent/test_agent_injection.py` |
| Offline replay of at least ten questions with golden figures, two refusals, written by hand and marked | `tests/agent/replays/` (16 files); `tests/agent/test_agent_replays.py` |
| `ask` live with a key and a clear stop without; `session` writes one transcript per question | `cli.py`, `transcript.py`; `test_agent_cli.py` (also a real subprocess without the key), `test_agent_transcript.py` |
| docs/agent.md: the loop, the tools, the answer shape, the refusal rules, how to run live, what to check in the transcripts | [docs/agent.md](../../docs/agent.md); `test_agent_docs.py` |
| `uv run ruff check . && uv run ruff format --check . && uv run pytest -q && python3 scripts/commit_hygiene.py` | Verification above |

## The replayed questions

Sixteen replays under `tests/agent/replays/`, written by hand from the tool definitions and marked as such in every file (`provenance: hand-written`, a note that no model produced them, and the replay model's name `replay (hand-written turns, not model output)`). They are not model output; they prove the loop, the tools and the checks on the warehouse built from the committed snapshot. Each answer's figures were taken from the rows the tools return, then checked by the agent's own figure rule and by the test against `docs/golden/golden.json`.

### Answered (twelve)

| # | Question | Queries, tool calls | Golden comparison |
| --- | --- | --- | --- |
| 1 | What were EPA's net contract obligations in fiscal year 2025? | 1, 2 | `obligations.total`: golden 1697318332.65, reproduced 1697318332.65, equal |
| 2 | How many contract awards were active in fiscal year 2025? | 1, 3 | `awards.active_in_period.count`: golden 6016, reproduced 6016, equal |
| 3 | What was the average net obligation per active award in FY2025? | 1, 2 | `awards.average_net_obligation`: golden 282134.03, reproduced 282134.03135804523, equal |
| 4 | What share of net obligations went to the ten largest recipients in FY2025? | 1, 3 | `recipients.top10_reporting_share`: golden 0.449020, reproduced 0.4490202395623079, equal |
| 5 | What share of FY2025 contract obligations was competed? | 1, 2 | `(A + D + F) / total, from golden`: golden 0.929875, reproduced 0.9298753995520864, equal |
| 6 | How did net obligations move month by month across fiscal year 2025? | 1, 2 | 12 golden values, all equal |
| 7 | Who were the five largest recipients by net obligations in FY2025? | 1, 3 | 4 golden values, all equal |
| 8 | How much was obligated for work performed in California in FY2025? | 1, 3 | `obligations.pop_state.CA`: golden 238749959.14, reproduced 238749959.14, equal |
| 9 | Which NAICS industry received the most net obligations in FY2025, and how many awards were active in it? | 2, 4 | `obligations.naics.562910`: golden 768867751.92, reproduced 768867751.92, equal |
| 10 | How were FY2025 net obligations split by extent-competed code? | 1, 3 | 7 golden values, all equal |
| 11 | Which five states had the most net obligations for work performed there in FY2025? | 1, 2 | 5 golden values, all equal |
| 12 | Which five product and service codes had the most net obligations in FY2025? | 1, 4 | 5 golden values, all equal |

Golden values compared: 39, all equal (amounts to the cent, counts exactly, the two ratios at the golden's six places, half even), plus the competition rate against the golden ratio (A + D + F) / total, equal at six places: 0.929875. Detail of the multi-value rows:

- How did net obligations move month by month across fiscal year 2025? `01` 90321565.67; `02` 60384929.67; `03` 127304190.15; `04` 311975420.85; `05` 151198107.99; `06` 66843656.91; `07` 80816007.20; `08` 79344605.92; `09` 111430879.28; `10` 138011170.20; `11` 112460065.43; `12` 367227733.38.
- Who were the five largest recipients by net obligations in FY2025? `DBD6KDKXWN65` 141735682.10; `VHPJR3MJWV49` 136907984.75; `MHMPN3F73NX3` 115370521.15; `DK4TBEJRAR56` 66893110.69.
- How were FY2025 net obligations split by extent-competed code? `A` 936214658.91; `B` 59448927.17; `C` 45698086.33; `D` 628056676.64; `F` 14023227.19; `G` 13924143.85; `unreported` -47387.44.
- Which five states had the most net obligations for work performed there in FY2025? `CA` 238749959.14; `OH` 169208815.00; `VA` 160568186.52; `DC` 138024824.27; `TX` 102283702.42.
- Which five product and service codes had the most net obligations in FY2025? `F108` 716008251.57; `R499` 226716569.88; `R429` 101778367.66; `F999` 67542562.18; `F115` 51595004.66.

The answers, as the replays end and the agent kept them:

1. *What were EPA's net contract obligations in fiscal year 2025?* EPA's net contract obligations in fiscal year 2025 (2024-10-01 to 2025-09-30) were $1,697,318,332.65. The figure is net: de-obligations count as negative amounts.
2. *How many contract awards were active in fiscal year 2025?* 6,016 contract awards were active in fiscal year 2025, meaning each had at least one transaction in the period. This counts awards active in the period, not new awards.
3. *What was the average net obligation per active award in FY2025?* The average net obligation per active award in fiscal year 2025 was $282,134.03: net obligations divided by the number of awards active in the period.
4. *What share of net obligations went to the ten largest recipients in FY2025?* The ten largest recipients, counted at the parent level, held 44.9% of net obligations in fiscal year 2025 (a share of 0.449020).
5. *What share of FY2025 contract obligations was competed?* 93.0% of net obligations in fiscal year 2025 were on competed transactions, a competition rate of 0.929875. Transactions with no extent-competed code count as not competed.
6. *How did net obligations move month by month across fiscal year 2025?* Net obligations were lowest in fiscal month 2 (November 2024) at $60,384,929.67 and highest in fiscal month 12 (September 2025) at $367,227,733.38. Fiscal month 4 (January 2025) also stood out at $311,975,420.85. Fiscal month 1 is October 2024.
7. *Who were the five largest recipients by net obligations in FY2025?* At the parent level, the five largest recipients in fiscal year 2025 were ENVIRONMENTAL QUALITY MANAGEMENT, INC (DBD6KDKXWN65) with $141,735,682.10, SEVENSON ENVIRONMENTAL SERVICES, INC. (VHPJR3MJWV49) with $136,907,984.75, ENVIRONMENTAL RESTORATION LLC (MHMPN3F73NX3) with $115,370,521.15, EASTERN RESEARCH GROUP INC (DK4TBEJRAR56) with $66,893,110.69, and WESTON SOLUTIONS HOLDINGS, INC. (ML53DYBW2523) with $64,159,150.30.
8. *How much was obligated for work performed in California in FY2025?* Net obligations for work performed in California (CA) in fiscal year 2025 were $238,749,959.14.
9. *Which NAICS industry received the most net obligations in FY2025, and how many awards were active in it?* NAICS 562910 (REMEDIATION SERVICES) received the most net obligations in fiscal year 2025, $768,867,751.92, and 1,194 awards were active in it during the year.
10. *How were FY2025 net obligations split by extent-competed code?* Code A (FULL AND OPEN COMPETITION) had the most net obligations in fiscal year 2025, $936,214,658.91, followed by code D (FULL AND OPEN COMPETITION AFTER EXCLUSION OF SOURCES) with $628,056,676.64. Transactions with no extent-competed code were net negative at -$47,387.44.
11. *Which five states had the most net obligations for work performed there in FY2025?* The five states with the most net obligations for work performed there in fiscal year 2025 were CA ($238,749,959.14), OH ($169,208,815.00), VA ($160,568,186.52), DC ($138,024,824.27) and TX ($102,283,702.42).
12. *Which five product and service codes had the most net obligations in FY2025?* The five product and service codes with the most net obligations in fiscal year 2025 were F108 (ENVIRONMENTAL SYSTEMS PROTECTION- ENVIRONMENTAL REMEDIATION) with $716,008,251.57, R499 (SUPPORT- PROFESSIONAL: OTHER) with $226,716,569.88, R429 with $101,778,367.66, F999 (OTHER ENVIRONMENTAL SERVICES) with $67,542,562.18 and F115 with $51,595,004.66.

Notes:

- Question 7 (top five recipients): the catalog's `recipient` is the parent level (ADR-0002). Four of the five figures equal the publisher's golden recipient values to the cent (DBD6KDKXWN65, VHPJR3MJWV49, MHMPN3F73NX3, DK4TBEJRAR56). The publisher's fourth, WESTON SOLUTIONS INC (Z4XGALW7L9K6, 84,868,589.24), is a recipient-level total: 115 of its 212 transactions roll up to the parent ML53DYBW2523 (64,253,671.53), 90 to RBRXSHXKFCB5 (21,588,769.79), 1 to XA9SVRU7K217 (-50,000.00), and 6 stay under its own UEI (-923,852.08). So the parent-level fifth is WESTON SOLUTIONS HOLDINGS, INC. (ML53DYBW2523, 64,159,150.30), which has no golden value. The replay pins this as a known difference; see the catalog candidate below.
- Question 12 first asks for every PSC code; 342 rows exceed the tool's 200-row limit, the tool returns `row_limit_exceeded`, and the replay recovers with `order_by` and `limit`.
- Question 9 makes two calls in one turn (a `schema` lookup and a second `run_query`); the loop answers both in one message, in order.

### Refused (four)

| # | Question | Code | How |
| --- | --- | --- | --- |
| 1 | How many new contract awards did EPA make in fiscal year 2025? | `unknown_metric` | the compiler refused the query; its refusal is attached |
| 2 | Break down FY2025 obligations by the recipient's city. | `unknown_dimension` | declared from the catalog, no query |
| 3 | What did EPA obligate on contracts in fiscal year 2023? | `outside_coverage` | declared from the catalog, no query |
| 4 | What is the top-10 supplier concentration for each recipient? | `dimension_not_allowed` | the compiler refused the query; its refusal is attached |

The reasons, as kept:

1. The catalog has no metric for new awards: award_count counts awards active in the period, which is a different number.
2. The catalog has no dimension for a recipient's city: recipients are identified by their parent UEI only, and the data holds no recipient address.
3. The warehouse holds fiscal year 2025 only (2024-10-01 to 2025-09-30), so it has no obligations for fiscal year 2023.
4. supplier_concentration_top10 cannot be grouped by recipient: within a single recipient, the ten largest recipients would always hold the whole share.

## The owner's live run, in the morning

From the repository root, with your own key in the shell (nothing in the repository holds or needs one):

```bash
export ANTHROPIC_API_KEY=...
```

```bash
git fetch origin s4-analyst-agent && git switch s4-analyst-agent
```

```bash
uv sync --frozen
```

```bash
uv run python -m agentic_bi.agent ask "What were EPA's net contract obligations in fiscal year 2025?"
```

```bash
uv run python -m agentic_bi.agent session --questions tests/agent/live-questions.txt --out data/tmp/agent-session-1
```

- The first command that needs the warehouse builds it into `data/tmp/warehouse.duckdb` from the committed snapshot when the file is missing (a few seconds).
- `tests/agent/live-questions.txt` holds the sixteen replay questions in order; each live transcript `data/tmp/agent-session-1/NN-<slug>.md` can be read next to the replay `tests/agent/replays/qNN-*.json`. Start with `data/tmp/agent-session-1/session.md`.
- `--model <id>` or `AGENTIC_BI_MODEL` runs another model; `--effort` changes the effort. A rerun needs a new `--out` directory (transcripts are never overwritten).
- What to check is listed in [docs/agent.md](../../docs/agent.md#what-to-check-in-the-transcripts): the query matches the question; the figures are the right rows read the right way; each refusal names a real gap; no tool text was followed as an instruction; no near-match substitution; any `[harness check]` correction; model, settings and cost.

## What changed

All inside the brief's owned paths.

- `src/agentic_bi/semantic/query.py`, `compiler.py`: `order_by` and `limit` only (schema, `OrderBy`, `check_ordering`, rendering, the bound `LIMIT ?`). Queries without them compile and serialise exactly as before.
- `tests/semantic/test_ordering.py` (new) and one assertion of `tests/semantic/test_query.py` (the schema's field set now lists `order_by` and `limit`).
- `docs/catalog.md`: the structured query section (two rows, the ordering rules, a top-5 example kept equal to the compiler by a test).
- `docs/decisions/0002-structured-query-and-compiler.md`: an Amendment section at the end.
- `docs/decisions/0005-analyst-agent-runtime.md` (new).
- `pyproject.toml`, `uv.lock`: `anthropic==2.3.0`.
- `src/agentic_bi/agent/` (new): `tools.py` (the four tools, their definitions, the `ToolBox`), `envelope.py` (the data delimiter), `loop.py` (the tool runner), `answer.py`, `final.py`, `figures.py` (the typed answer and its checks), `model.py` (request shape, live and replay models), `prompt.py` and `prompts/analyst-v1.md` (the versioned system prompt), `settings.py`, `cli.py`, `transcript.py`, `__main__.py`.
- `tests/agent/` (new): fixtures (a synthetic schema with awkward labels, the warehouse built from the snapshot), sixteen replays and their test, and tests for the tools, the delimiter, the figure rule, the final reply, the loop, the injection case, names with numbers, the model adapters, the prompt, the command line, transcripts and the docs; `live-questions.txt`.
- `docs/agent.md` (new), this report.

## Choices made inside the brief

The owner may want to rule on these; each is easy to change.

1. **Runtime.** The Messages API with the project's own loop, not the SDK's beta `tool_runner` and not the Agent SDK (ADR-0005 gives the reasons: offline proof without a key or an HTTP mock, the control the brief asks for, no built-in tools to switch off).
2. **One ordering key.** `order_by` is one `{field, direction}`; ties fall back to the default order, so a cut is deterministic. A `limit` without `order_by` is refused, so a top-N is never "the first rows by accident". Bad values are `invalid_query` with the field named; no new refusal class was added (`errors.py` is outside the owned paths).
3. **The final reply is JSON, checked by the loop.** One correction is allowed, then the question fails. Structured outputs and strict tool schemas were left off because they could not be tested live tonight; `build_request` is the one place to add them.
4. **The figure rule is strict.** Every number must match a metric cell at its written precision and keep its sign, or be a plain whole number that frames a query (a grain or dimension value, a numeric filter value, the limit, the top-n n, the year of a time range or of the coverage), or a year the question wrote. Dates are checked as dates, in ISO form or in words. The model may not compute (sums, differences, ratios, multiples, growth rates), write ranks or counts of rows in digits, or repeat a figure the question states as a premise. A name the schema tool returned, written exactly as returned, is text for a bare whole number in it (ten real labels hold one); any other number in a name is checked. One limit is documented: a worked-out whole number equal to a frame number cannot be told apart from it. Accounting parentheses, "(450)", are not read as negative; the prompt asks for the minus sign.
5. **Refusals.** The compiler's codes plus `outside_coverage`. A refusal may come from the compiler (attached as evidence) or be declared from the catalog without a query; the verifier (S5a) and the owner judge declared ones.
6. **Limits.** 200 rows per query (more is an error asking for a narrower query or a top-N), 12 model calls, 16 tool calls, 2 final replies per question; effort `high`; summarized thinking in transcripts; prompt caching on; no server-side fallback model. A rejected key, a key without access or an unknown model stops a session (exit 2); a 400, a rate limit after the SDK's retries, a server error or a network failure fails that question only.
7. **The schema tool's lookups** run fixed SQL over the contract's dimension tables through the runner's own guard (`agentic_bi.semantic.runner._run`, imported by its private name, because `runner.py` is outside the owned paths). A later session could make that guard public.
8. **The command line builds the warehouse** into `--database` when the file is missing, after the key check.

## Catalog candidate (stop rule)

The plan's "top five recipients" is expressible with `order_by` and `limit`, at the parent level the catalog defines. The publisher's own recipient ranking (golden `obligations.recipient.*`, by `recipient_uei`) is not: it needs a child-level recipient dimension, for example `direct_recipient` on `fct_contract_transaction.recipient_uei` with names from `dim_recipient`. Today four of the publisher's five agree with the parent level to the cent and one (Z4XGALW7L9K6) cannot be reproduced. Not added: the brief forbids catalog changes, and ADR-0002 fixes the parent level.

## Open questions

1. **The `invalid_query` row of the Refusals table** in `docs/catalog.md` still describes only schema errors; it could mention a bad `order_by` and a `limit` outside 1 to 100. That table is outside the section this brief owns.
2. **S5a's question set** marks its top-N questions `requires: top_n`. The shape is now `"order_by": {"field": ..., "direction": "asc" | "desc"}` with `"limit": 1..100`; S5b should align the two.
3. **The live request shape is unproven** until the owner's run: adaptive thinking with summarized display, effort, top-level prompt caching and the tool schemas (the structured-query schema uses `oneOf`, `format` and `pattern`) are documented for the pinned SDK and model but were not sent to the API tonight (no key, no network).
4. **Push allow list.** `.claude/settings.json` allows `git push -u origin s1-` and `s3a-` only. The push of `s4-analyst-agent` went through in this session; a later unattended session may need an `s4-` entry.
5. **Session setup.** The container's git identity was set to the maintainer's in the repository's local config, as the prompt asked. The prompt named the branch `s4-analyst-agent`, so that branch was used instead of the session's default branch name.
6. **Warnings.** The 108 pydantic deprecation warnings come from inside SQLMesh, as in S2; unchanged.
7. **README.** It does not yet link `docs/agent.md` (outside the owned paths; S6).

## Independent review

Three sonnet subagents drafted the unit tests from the code (tools and envelope; figure rule and final reply; model, prompt and command line), each pinning a suspected defect as a strict xfail with the input that shows it. All three also ran mutation checks on scratch copies outside the repository (55, 48 and about 150 deliberate one-line changes; everything was caught except equivalent changes, and the untested branches they found were then covered). They found 13 defects, all fixed on this branch, and their tests stay as regression tests: a coverage list shared with the caller; codes cut off by the limit reported as not found; text with an unpaired surrogate reaching DuckDB and breaking UTF-8; "2.5x" read as "2"; a Decimal NaN cell stopping the check; an empty `--model` ending in a traceback; one replay model unable to serve a session; a foreign error made its own cause; "$9.9BN" passing unchecked after the first fix; the second number of "10-15%" never read; a UTF-8 byte order mark turning a comment into a question; a NaN timeout accepted; `ReplayModel.exhausted` true after a failed check.

An opus subagent that wrote none of the code then reviewed the whole diff against the brief's `done_means` and the Code Review Rules, and twice more checked the fixes. Its findings and what was done:

| # | Finding | Severity | Resolution |
| --- | --- | --- | --- |
| 1 | A hostile name repeated in an answer could carry an invented amount past the figure check; the injection test did not exercise the name masking | blocking | Fixed: a name exempts only a bare whole number, written exactly as returned; glued scales, amounts, percents and decimals in a name are checked; the injection test passes the names and repeats the whole hostile name |
| 2 | A model-chosen tool name rendered as Markdown (image, link) in the transcript view | blocking | Fixed: model and data fields are code spans; the question heading is escaped; model text bodies stay fenced |
| 3 | Worked-out figures passed (multiples, ranks up to the row count, coverage months and days, amounts and counts from the question, "USD9.9 billion") | should fix | Fixed: no rank rule, years only, question years only, multiples and USD amounts read; one limit documented (a worked-out whole number equal to a frame number) |
| 4 | An unpaired surrogate stopped transcript writing and terminal output | should fix | Fixed: escapes in printable output and in written files |
| 5 | docs/agent.md had lost the `\u003c` escapes | should fix | Fixed, with a test that checks the page against the envelope |
| 6 | PSC codes such as 7B20 and names such as "30MM" read as amounts | should fix | Fixed: a whole number glued to letters is a code |
| 7 | Correct answers rejected ("$1.7 bn", "$1.7 B", "30 September", "2024-25") | note | Fixed; a name in another case or without its legal suffix is now refused on purpose (see 1); "(450)" stays unsupported |
| 8 | ADR-0005 marked Accepted | note | Now Proposed, as is the ADR-0002 Amendment |
| 9 | One 400 stopped the whole session | note | Fixed: a 400 fails one question |
| 10 | The test fixture's docstring said the base URL was unset | note | Fixed: it is unset |
| 11 | No report on the branch | note | This file |
| 12 | The dependency is pinned with hashes; `httpx2` and `httpcore2` are new names | note | They are `anthropic` 2.3.0's declared requirements (its HTTP stack), resolved from PyPI with hashes; worth a look by the owner |

The reviewer checked the fixes twice more, on `37a542c` and on `077e600`, re-running its probes. The follow-up findings were all fixed: a glued scale in a hostile name (`9BN`, `500K`) and a name turned into an ordinary sentence by a suffix-stripped or case-folded match (blocking, fixed in `077e600`: a name now counts only as returned, and only for a bare whole number); a whole-number premise of the question ("Yes, 7,000"); decimals with unknown tails; "4 times"; two false rejections ("2024-25", "30 September"); a bare URL in a tool name becoming a link. The last check closed all of them and found one regression of the previous fix ("the top 5 may change" read as a date) and four minor notes (a year-shaped count in the question, "1,000s", "2024-10", and a correction message that did not say why a name was not read as one); all five were fixed in `498477c`.

What stays, by design and documented in `figures.py` and `docs/agent.md`: a whole number the model works out that equals a frame number cannot be told apart from it (the verifier and the owner's check cover it); a name must be written exactly as the schema tool returned it, so an answer that changes a name's case is sent back once, with the reason; accounting parentheses are not read as negatives.

## Blocked

Nothing.
