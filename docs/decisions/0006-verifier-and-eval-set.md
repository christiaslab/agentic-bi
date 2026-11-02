# ADR-0006: The verifier's contract and the eval question set

Status: Proposed, 2026-11-02 (S5a). Serves ADR-0002 points 3 and 8. The owner accepts or amends it when merging S5a.

## Context

ADR-0002 point 8 asks for a verifier: a cheaper model that checks a question against its structured query and the catalog, judging intent, not SQL. The compiler already refuses anything outside the catalog (point 3), so a query that compiles can still answer the wrong question: the wrong metric, a missing filter, the wrong months. That is the failure the verifier exists to catch.

S5b scores the analyst agent on intent accuracy and refusal behaviour (ADR-0002, Consequences). It needs a question set with an expected structured query or an expected refusal per question, and a verifier whose contract is fixed before any live run. No API key exists in the cloud sessions, so the contract must be provable offline.

## Decision

1. **Input.** The verifier takes three things: the question (text, at most 2,000 characters), the structured query (the decoded JSON value the agent emitted, possibly with `order_by` and `limit`), and a catalog excerpt. The excerpt is built from `catalog/metrics.yaml` and the slice of ADR-0001: every metric with its description, unit and allowed dimensions; every dimension with its description, type and fixed values; the grains; the operators; the ranking rule; the slice (EPA, award types A to D, fiscal year 2025). It holds no table or column name.

2. **It judges intent, never SQL.** The judge never sees compiled SQL, never writes it and never computes a number. Equivalent queries are equal for the judge: a null time range and the full fiscal year 2025 range, `in` with one value and `eq`, filters or dimensions in another order.

3. **Deterministic checks run first.** Before any model call the verifier checks, in order: the structured-query JSON schema (`parse_query`), the catalog (`compile`), and the slice (the time range must lie inside 2024-10-01 to 2025-09-30). A failure decides the verdict and no model is called. These need no model and must never cost a call.

4. **Output.** A verdict is one of:
   - `consistent`: the query answers the question as the catalog defines it; no issue;
   - `inconsistent`: the question is answerable but the query answers another one; at least one issue;
   - `refuse_expected`: the question cannot be answered in the catalog or the slice, so any query is a substitute.

   Each issue has a typed `code`, the `field` of the query it concerns and a one-sentence explanation. A verdict also names its stage (`deterministic` or `model`), its judge and the prompt version.

5. **Issue codes.** Deterministic codes, raised only by the checks:

   | Code | Raised when | Verdict |
   | --- | --- | --- |
   | `invalid_query` | the query fails the structured-query schema; the compiler's refusal code is kept beside it | `inconsistent` |
   | `outside_catalog` | a metric, dimension, grain, operator or filter value is outside the catalog or not allowed for the metric | `inconsistent` |
   | `time_range_outside_slice` | the time range reaches dates the warehouse does not hold, wholly or in part | `refuse_expected` when it is the only issue |

   Model codes, the only ones the judge may use:

   | Code | Meaning |
   | --- | --- |
   | `wrong_metric` | the metric measures something else than the question asks for |
   | `wrong_dimension` | a breakdown is missing, or one is added the question does not ask for |
   | `missing_filter` | a restriction in the question is missing from the query |
   | `wrong_filter` | a filter the question does not ask for, or the wrong field, operator or value |
   | `wrong_grain` | the time breakdown differs from the question's |
   | `wrong_time_range` | the dates differ from the period the question names |
   | `wrong_order` | the ranking (`order_by`, `limit`) does not match a top-N or bottom-N question, or is added to a question without one |
   | `should_refuse` | the question cannot be answered as the catalog defines its terms |

   Rules between verdict and codes: `consistent` has no issue; `inconsistent` has at least one and never `should_refuse`; `refuse_expected` has `should_refuse` (model) or `time_range_outside_slice` (deterministic).

6. **The model's answer is parsed against a JSON schema.** `VERDICT_JSON_SCHEMA` (`src/agentic_bi/verifier/verdict.py`) lists the fields, the verdict values and the model codes. The answer must be one JSON object, optionally inside one code fence, with no repeated key. Anything else, including a valid object that breaks the rules of point 5, is a `MalformedVerdictError`. A malformed verdict is an error, never a pass. The schema validator is a small one in the module, written for the keywords the schema uses, so no dependency is added; it refuses a schema keyword it does not know.

7. **The prompt is a versioned file.** `src/agentic_bi/verifier/prompts/verifier-v1.md` opens with `prompt_version: v1`, and `PROMPT_VERSION` must match. A change of meaning is a new file and a new version. Every verdict names the version it answered, and a replay recorded for one version is never served for another. The question, the query and the excerpt reach the model as JSON between tags, with `<`, `>` and `&` escaped, and the prompt says the blocks are data (ADR-0002 point 9 applied to the verifier).

8. **The model is called only through a `Judge`.** A judge turns a request into raw text; the verifier alone parses and decides. `ReplayJudge` serves recorded verdicts (tests, no key). `AnthropicJudge` makes one Messages API call with the versioned system prompt and low effort; it receives its client by injection, or imports the `anthropic` library lazily when a key is present. No dependency is added here: the library arrives with S4 (ADR-0005) and S5b wires the live judge into the eval run with the pinned model and the spend cap of the plan.

9. **Default model: `claude-haiku-5-5`.** The task is a short classification against a definition the prompt carries in full, which is what the smallest current model is for; ADR-0002 asks for a cheaper model than the agent's. The id is a setting (`AGENTIC_BI_VERIFIER_MODEL` or `--model`), not a constant in the prompt. S5b measures it against the question set; if it misjudges, the next step is a larger model, not a looser parser.

10. **Top-N before S4.** `order_by` and `limit` reach the structured query with S4's amendment to ADR-0002. Until then the checks read them by a provisional rule taken from the S4 brief (`order_by` is `{field, direction}` with the metric or a requested dimension, `asc` or `desc`; `limit` is 1 to 100; a limit needs an order) and check the rest of the query as usual. Once `parse_query` accepts them, the native schema and compiler decide and the provisional rule is not reached. If S4 picks another shape, S5b aligns the question set and drops the provisional rule.

11. **The eval question set.** `evals/questions.yaml` holds 40 to 60 questions, written by hand. Each has an expected outcome, `answer` with its expected structured query or `refuse` with a reason (`out_of_catalog`, `outside_slice`, `ambiguous`) and a note; optional fields mark top-N questions (`requires: [top_n]`), paraphrase groups, golden links to `docs/golden/golden.json` and catalog candidates. Tests keep it covering every metric, grain, dimension and operator, at least ten top-N questions, at least eight refusals, at least five paraphrase pairs with one expected query, and every expected query that needs no top-N passing the current schema and compiling. Its fields and S5b's scoring are in `evals/README.md`.

## Consequences

- A query that fails the schema, leaves the catalog or leaves the slice is caught for free, before any spend.
- The live verdict quality is unproven until S5b: the replays are written by hand and prove the plumbing, not the model. S5b runs the judge on the expected queries (they should come back `consistent`) and on deliberately wrong ones (each should come back with its code) before trusting it on the agent's queries.
- Two vocabularies of refusal exist on purpose: the compiler's (a query outside the catalog) and the verifier's `should_refuse` (a question outside the catalog that a valid query tries to answer anyway). Only the second needs a model.
- A question set written by the same hands as the catalog can share its blind spots. Paraphrase pairs and plain-language wording reduce that; live questions from the owner (S4) should be added to it over time, with an id each and never renumbered.
