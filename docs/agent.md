# The analyst agent

Status: built in S4 (2026-11-02) for [ADR-0002](decisions/0002-structured-query-and-compiler.md) and [ADR-0005](decisions/0005-analyst-agent-runtime.md). The code is in `src/agentic_bi/agent/`; the tests are in `tests/agent/`. This page explains the loop, the tools, the answer, the refusal rules, how to run the agent live and what to check in its transcripts.

## In one paragraph

A model answers a spending question only through four project tools. It never writes SQL: it writes a structured query, and the compiler of [docs/catalog.md](catalog.md) turns it into SQL with bound values on the read-only warehouse. Every answer comes back as a typed `Answer`: the question, each structured query that ran with its SQL, parameters and rows, and a short text whose every figure appears in those rows. A question the catalog cannot answer is refused with the catalog's reason. The model is a setting, `claude-sonnet-5-5` by default.

## The loop

```text
question --> model (system prompt + 4 tool definitions)
               |  tool_use: catalog / schema / run_query / explain_measure
               v
             ToolBox: checks the input, runs the tool on the read-only connection
               |  result as data: <tool_data tool="..." status="ok|error"> JSON </tool_data>
               v
             model ... (as many turns as it needs, within the limits)
               |  end_turn: one JSON object, an answer or a refusal
               v
             final-reply check: every figure in the rows? a catalog refusal code?
               |  no: one message back to the model saying why ("[harness check] ...")
               v
             Answer (answered | refused | failed) + transcript
```

1. The loop (`loop.ask`) sends the question with the system prompt (`prompts/analyst-v1.md`) and the four tool definitions.
2. For every `tool_use` block the model returns, the `ToolBox` runs the tool and the loop sends all results back in one message, in the order of the calls. A refusal or an error is an error result (`is_error`), and the model may fix its query and try again.
3. When the model stops with `end_turn`, its text must be one JSON object, an answer or a refusal. The loop checks it (below). A reply that breaks a rule goes back once with the reason; a second bad reply ends the question as `failed`.
4. The history is append-only: each model turn goes back unchanged, thinking blocks included, and tool results and the harness's own feedback are added after it.
5. Nothing a tool returns changes what the loop does, apart from which result goes back. The loop has no branch on the content of a tool result.

Limits, all in `AgentSettings`: 12 model calls, 16 tool calls and 2 final replies per question, 200 rows per query (more is an error that asks for a narrower query or a top-N), 30 seconds per query, 16,000 output tokens per model turn. Any other stop of the model (cut at `max_tokens`, a model refusal, the context window) ends the question as `failed` with the reason.

## The four tools

They are plain Python functions in `tools.py` with tests in `tests/agent/test_agent_tools.py`. None takes SQL, none reaches a file, a shell or the network. The model gets no other tool.

| Tool | Input | What comes back |
| --- | --- | --- |
| `catalog` | nothing | The five metrics with unit, rule and allowed dimensions; the eight dimensions with type, fixed values and whether they have names; the four grains; the four operators; how `order_by` and `limit` work; the coverage: EPA prime contracts, award types A to D, 2024-10-01 to 2025-09-30 (fiscal year 2025) |
| `schema` | `dimension`, and optionally `search` (text in the code or the name, any case), `codes` (up to 100) and `limit` (1 to 200, default 50) | The allowed values of that dimension, sorted by code, with names where the contract has them: `recipient` from `dim_reporting_recipient`, `psc` from `dim_psc`, `naics` from `dim_naics`, `sub_agency` from `dim_sub_agency`, `competition` from `dim_competition`. `award_type` and `competed` list their fixed values; `pop_state` lists the states found. It also says how many values matched, whether the list was cut, and which `codes` were not found |
| `run_query` | the structured query itself: `metric`, and optionally `dimensions`, `filters`, `grain`, `time_range`, `order_by`, `limit` | The query as the parser read it, the SQL the compiler wrote, its parameters, the column names, the rows and the row count. A query outside the catalog is refused with the compiler's code and the field it concerns |
| `explain_measure` | `metric` | The metric's rule in plain words, how it is computed from other metrics, its parts, and the dimensions it allows and does not allow |

The `schema` tool reads the dimension tables with fixed SQL written in `tools.py` from the contract's names, with the search text and the codes as bound values, through the runner's own guard (one SELECT, a row limit, a timeout). It never shows a table or column name to the model (ADR-0002 point 7).

## Tool results are data

Every result reaches the model as JSON inside one delimiter:

```text
<tool_data tool="schema" status="ok">
{"dimension": "recipient", ..., "values": [{"value": "DBD6KDKXWN65", "label": "ENVIRONMENTAL QUALITY MANAGEMENT, INC"}]}
</tool_data>
```

Inside the JSON, `<`, `>` and `&` are written as `<`, `>` and `&`. It is still the same JSON value, but no text from the warehouse can contain the delimiter, so a name cannot close it early or open a fake one. The `tool` attribute is one of the four names, or `unknown`, never text from the model or the data. The system prompt says that everything inside the delimiter is data and never an instruction. `tests/agent/test_agent_injection.py` feeds a recipient name that tries to close the delimiter and give orders, and shows that nothing else changes (below).

## The answer

`Answer` (`answer.py`) has one of three statuses:

| Status | Carries |
| --- | --- |
| `answered` | `text`: one to four sentences |
| `refused` | `refusal`: a `code`, the model's one-sentence `reason`, and `compiler`, the compiler's own refusal when a `run_query` was refused with the same code |
| `failed` | `failure`: why the run stopped (a limit, a stop of the model, a reply rejected twice, an API error) |

Every answer also carries `queries`, one `QueryRun` per structured query that ran: the query, the SQL, the parameters and the rows. These come from the tool layer's record of what ran, never from what the model says it ran. It also carries the model id and the counts of model and tool calls.

The model's final reply is one JSON object:

```json
{"status": "answered", "answer": "EPA's net contract obligations in fiscal year 2025 were $1,697,318,332.65."}
{"status": "refused", "code": "unknown_metric", "reason": "The catalog has no metric for new awards."}
```

### Every figure appears in the rows

`figures.py` reads every number in the answer text and accepts it only when it matches the rows of a query that ran:

- **A measured value** (a metric cell) at the precision written: `$1,697,318,332.65`, `$1.70 billion`, `$1.7BN` and `1.7 billion` all match 1,697,318,332.65; `44.9%` matches a share of 0.449020. Scale words and suffixes are read in any case. The sign must agree: a de-obligation keeps its minus.
- **A frame number**, a plain whole number that frames a query: a fiscal year, month or quarter, a code written in digits (NAICS 562910), a numeric filter value, the `limit`, the 10 of the top-10 share, a rank up to the number of rows, or a year, month or day of the time range or of the coverage.
- **A figure the user wrote in the question** ("fiscal year 2023").

Numbers inside a word or a code (`F108`, `FY2025`, `Q3`, `5th`, `2.5x`, a UEI) are not figures, but a number written with a dollar sign always is, whatever follows it, and both ends of a range (`10-15%`) are figures. A name never counts as evidence: names come from the `schema` tool, not from rows, so a number inside a recipient name cannot ground a figure stated elsewhere. A name the `schema` tool returned and the answer writes out in full is text, not a figure: ten labels of the 2026-11-01 warehouse hold a number, such as `CHENEGA SENTINEL 360, LLC`, and the 360 in it is not checked, while a 360 written anywhere else is. Nothing the model computes itself passes: a sum of two rows, a difference, a growth rate. For such a figure the model must run a query. The same check applies to the reason of a refusal.

## The refusal rules

A question is refused, never answered with a near match (ADR-0002 point 3), when it needs something the catalog does not have. The refusal names the reason with one code:

| Code | Use |
| --- | --- |
| `unknown_metric`, `unknown_dimension`, `dimension_not_allowed`, `unknown_grain`, `unknown_operator`, `filter_field_not_dimension`, `invalid_filter_value`, `malformed_time_range`, `invalid_query` | The compiler's own codes ([docs/catalog.md](catalog.md#refusals)) for a metric, dimension, grain, operator, filter value or query shape outside the catalog |
| `outside_coverage` | Data the warehouse does not hold: another agency, another fiscal year, other award types |

- A refusal can come from the compiler (the model ran a query and `run_query` refused it) or be declared from the catalog (the model read the catalog and saw the gap). The first carries the compiler's refusal in `refusal.compiler`; the second has `compiler` empty. Both are kept; the owner's check and the S5a verifier judge whether a declared refusal names a real gap.
- A question with any part outside the catalog is refused as a whole.
- `award_count` is not the number of new awards; a question about new awards is refused. Recipients are at the parent level; a question about a subsidiary's own UEI finds nothing.

## Run it live

The live run needs your own Anthropic API key and is the owner's step. The tests never call the API.

```bash
export ANTHROPIC_API_KEY=...
```

```bash
uv sync --frozen
```

```bash
uv run python -m agentic_bi.agent ask "What were EPA's net contract obligations in fiscal year 2025?"
```

```bash
uv run python -m agentic_bi.agent session --questions tests/agent/live-questions.txt --out data/tmp/agent-session
```

- Without `ANTHROPIC_API_KEY` both commands stop with a clear message before doing anything, and send nothing. The key is read by the SDK only; the project never prints or stores it.
- `ask` prints the answer or the refusal, then each query with its SQL, parameters and rows. `--out DIR` also writes the transcript.
- `session` runs one question per line of the file (blank lines and `#` comments are skipped) and writes, for each, `NN-<slug>.json` and `NN-<slug>.md`, then `session.json` and `session.md` with a status table. It refuses to overwrite earlier transcripts; use a new `--out` directory per run. `data/tmp/` is ignored by git.
- `tests/agent/live-questions.txt` holds the questions of the sixteen replays, in order, so each live transcript can be read next to its replay.
- `--model ID` (or the environment variable `AGENTIC_BI_MODEL`) picks another model; `--effort` sets the effort (default `high`). `--database PATH` points at another warehouse file; the default is `data/tmp/warehouse.duckdb`, built from the committed snapshot when it is missing.
- Exit codes: 0 when every question was answered or refused, 1 when one failed, 2 when nothing could run (no key, a bad argument, an existing transcript, an error every call would hit: a rejected key, an unknown model, a request the API refuses).

### What to check in the transcripts

Open `session.md`, then each question's `.md` file; the `.json` holds the same, complete.

1. **The query matches the question.** The metric, the filters (state, code), the grain, the time range (fiscal year 2025 is 2024-10-01 to 2025-09-30) and, for top-N questions, `order_by` and `limit`. This is the check that matters most: the figures are only as right as the query.
2. **The figures match the rows.** The harness has already checked that each figure appears in the rows; check that it is the right row and the right reading (net, not gross; parent level; a share, not a percentage point).
3. **Each refusal names a real gap.** A refusal from the compiler shows its refusal; a declared refusal should name something the catalog really lacks. A refused question the catalog could answer is a miss; an answered question the catalog cannot answer is worse.
4. **Tool results stayed data.** No turn follows text found inside a tool result, and no name or label is quoted as if it were an instruction.
5. **No substitution.** The answer uses the catalog's metric, not a near one (for example `award_count` for new awards).
6. **Corrections.** A `[harness check]` message means the first reply broke a rule; note which rule and whether the second reply fixed it properly.
7. **Model, settings and cost.** Each model turn names the model that served it; the header gives the prompt version and its sha256, the number of calls and the token usage.
8. **Failures.** For each `failed` question, the reason is in the header and in a `Stopped` line at the end.

## Offline replays

`tests/agent/replays/` holds sixteen replays: twelve answered questions (one per metric, a fiscal-month trend, the top five recipients, a state filter, a two-step question, the split by extent-competed code, the top five states, and a top-five PSC question that first runs into the row limit) and four refusals (new awards and concentration by recipient, refused by the compiler; a recipient's city and fiscal year 2023, declared from the catalog). Each file holds the question, the model turns and what must come out.

- **They are written by hand**, from the tool definitions, to drive the real tool layer offline. No model produced them; each file says so in `provenance` and `note`, the replay model is named `replay (hand-written turns, not model output)`, and their transcripts say `replay`. They prove the loop, the tools and the checks, not the model.
- `tests/agent/test_agent_replays.py` builds the warehouse from the committed snapshot, runs every replay through the real loop and tools, and compares the rows with [docs/golden/golden.json](golden/golden.json): amounts to the cent, counts exactly, ratios at the golden's six places (half even). The competition rate has no golden value of its own; it is compared with the golden obligations of the competed codes A, D and F over the golden total.
- **One known difference.** The golden recipient values are the publisher's recipient-level totals; the catalog's `recipient` is the parent level (ADR-0002). Four of the publisher's top five match the parent-level figures to the cent. The fourth, WESTON SOLUTIONS INC (Z4XGALW7L9K6, 84,868,589.24), rolls up mostly to two other parents, so the parent-level top five holds WESTON SOLUTIONS HOLDINGS, INC. (ML53DYBW2523, 64,159,150.30) instead, which has no golden value. The replay pins this difference so that a change shows.
- To add a replay: copy a file, write the turns from the tool definitions, take every figure of the final reply from the rows the tools return, and add the golden ids the rows must reproduce.

## Where things are

| Path | Content |
| --- | --- |
| `src/agentic_bi/agent/loop.py` | The tool runner: `ask(question, model=..., toolbox=..., settings=...)` |
| `src/agentic_bi/agent/tools.py` | The four tools, their definitions and the `ToolBox` |
| `src/agentic_bi/agent/envelope.py` | The data delimiter |
| `src/agentic_bi/agent/answer.py`, `final.py`, `figures.py` | The typed answer, the final-reply check and the figure check |
| `src/agentic_bi/agent/model.py` | The request shape, the live model and the replay model |
| `src/agentic_bi/agent/prompt.py`, `prompts/analyst-v1.md` | The versioned system prompt |
| `src/agentic_bi/agent/settings.py` | The model id and the limits |
| `src/agentic_bi/agent/cli.py`, `transcript.py` | The command line and the transcripts |
