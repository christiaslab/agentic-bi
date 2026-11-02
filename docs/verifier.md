# The verifier

Status: built in S5a (2026-11-02) under [ADR-0006](decisions/0006-verifier-and-eval-set.md), for ADR-0002 point 8. The code is in `src/agentic_bi/verifier/`. This page explains it in plain words.

## What it answers

"Does this structured query answer this question, as the catalog defines its words?"

The compiler already refuses a query that leaves the catalog. A query that compiles can still answer the wrong question: net obligations where the question counted awards, all states where it asked about California, December where it asked about March. The verifier exists for that. It judges intent. It never sees SQL, never writes it and never computes a number.

## How it decides

```text
question + structured query
        |
  1. schema check      parse_query           fails -> inconsistent, invalid_query
  2. catalog check     compile               fails -> inconsistent, outside_catalog
  3. slice check       2024-10-01..2025-09-30 wholly outside -> refuse_expected
                                             partly outside -> inconsistent
                                             (both: time_range_outside_slice)
        |  all pass
  4. judge (model)     versioned prompt + catalog excerpt + question + query
        |
  5. parse_verdict     JSON schema + rules   malformed -> error, never a pass
        |
  consistent | inconsistent | refuse_expected, with typed issues
```

Steps 1 to 3 need no model and cost nothing. When one fails, the verdict is decided there and no model is called. Only a query that passes all three reaches the judge.

## Verdicts and issue codes

| Verdict | Meaning |
| --- | --- |
| `consistent` | The query answers the question as asked. No issue. |
| `inconsistent` | The question is answerable, but the query answers another one. At least one issue. |
| `refuse_expected` | The question cannot be answered in the catalog or the slice; the agent should refuse rather than substitute. |

| Code | Raised by | Meaning |
| --- | --- | --- |
| `invalid_query` | checks | The query does not match the structured-query schema |
| `outside_catalog` | checks | A metric, dimension, grain, operator or filter value outside the catalog, or not allowed for the metric |
| `time_range_outside_slice` | checks | Dates the warehouse does not hold. Wholly outside the slice, the verdict is `refuse_expected`; partly outside, it is `inconsistent`, because the query alone cannot show whether the question was outside the slice or the dates are simply wrong. |
| `wrong_metric` | judge | The metric measures something else than the question asks for |
| `wrong_dimension` | judge | A breakdown is missing, or one is added that the question does not ask for |
| `missing_filter` | judge | A restriction in the question is missing from the query |
| `wrong_filter` | judge | A filter the question does not ask for, or the wrong field, operator or value |
| `wrong_grain` | judge | The time breakdown differs from the question's |
| `wrong_time_range` | judge | The dates differ from the period the question names |
| `wrong_order` | judge | The ranking (`order_by`, `limit`) does not match a top-N or bottom-N question |
| `should_refuse` | judge | The question cannot be answered as the catalog defines its terms |

A deterministic issue from a compiler refusal keeps the compiler's own code beside it (`refusal_code`, for example `unknown_metric` or `dimension_not_allowed`), and every issue names the `field` of the query it concerns.

## What the judge sees

- **The system prompt**, `src/agentic_bi/verifier/prompts/verifier-v1.md`. Its first line is `prompt_version: v1`. It defines the verdicts and the eight model codes, the rules of comparison (fiscal months count from October; a null time range equals fiscal year 2025; `in` with one value equals `eq`; order of filters and dimensions does not matter), when to refuse (stated by class, not by example, so it does not echo the eval set's own refusal questions), and the output format.
- **The catalog excerpt**, built by `catalog_excerpt()` from `catalog/metrics.yaml` and the slice: metric rules and allowed dimensions, dimension meanings and values, grains, operators, the ranking rule. No table or column name.
- **The question and the query**, each as JSON between tags. `<`, `>` and `&` are escaped inside them, so a question cannot close its own tag, and the prompt says the blocks are data.

## Reading the judge's answer

The judge must answer with one JSON object that matches `VERDICT_JSON_SCHEMA` (`verdict.py`): `verdict`, `issues` (each with `code`, `field`, `explanation`) and `rationale`. One code fence around the whole answer is tolerated. Then the rules: `consistent` has no issue, `inconsistent` has at least one and never `should_refuse`, `refuse_expected` has `should_refuse`. Prose, a repeated key, an unknown field, a deterministic code, a blank rationale or explanation, text nested too deeply to parse, or a verdict that breaks a rule raises `MalformedVerdictError`. A malformed answer is an error, never a pass.

## Judges

A `Judge` turns a request into the model's raw text; the verifier parses it.

- `ReplayJudge` serves recorded answers from a directory of replay files, matched on the question, the query (key order ignored) and the prompt version. A request with no replay is an error. The replays in `tests/verifier/replays/` are written by hand and say so; they prove the plumbing, not the model.
- `AnthropicJudge` makes one Messages API call: the versioned system prompt, one user message, low effort. The default model is `claude-haiku-5-5`; `AGENTIC_BI_VERIFIER_MODEL` or `--model` changes it. It takes its client by injection (the tests pass a fake) or imports the `anthropic` library when built without one. That library arrives with S4; until it is installed, a live judge cannot be built and the command says so. Any error of the call itself (network, authentication, rate limit) becomes a `JudgeError` naming only the error's type: a failed run, never a verdict. S5b wires the live judge into the eval run with the pinned model and the spend cap.

## Top-N queries before S4

`order_by` and `limit` join the structured query with S4. Until then `parse_query` refuses them, and the checks read them by a provisional rule from the S4 brief: `order_by` is `{field, direction}`, with the field the metric or a requested dimension and the direction `asc` or `desc`; `limit` is an integer from 1 to 100 and needs an `order_by`. The rest of the query is checked as usual. Once the structured query accepts the two fields, the schema and compiler decide and this rule is never reached.

## Running it

```sh
uv run python -m agentic_bi.verifier check \
  --question "How much was obligated in March 2025?" \
  --query '{"metric": "obligations", "time_range": {"start": "2025-03-01", "end": "2025-03-31"}}'
```

It prints one JSON object: the deterministic checks, the verdict and a note.

- **No `ANTHROPIC_API_KEY`:** only the checks run. A query that fails one gets its deterministic verdict; a query that passes all three has `"verdict": null`, a note that intent was not judged, and exit status 3.
- **With a key:** a query that passes the checks goes to the live judge and the model verdict is printed.
- **`--replay <dir>`:** the replay judge answers instead, with no key and no network; the note names the replay and its provenance.

Exit status: 0 for `consistent`; 1 for `inconsistent` or `refuse_expected`; 2 when the run failed (no live judge available, a failed model call, a missing replay, a malformed verdict, an empty question); 3 when the checks passed and intent was not judged, so a script never reads an unjudged query as verified. The key is read by the client library from the environment and never printed.

From Python:

```python
from agentic_bi.verifier import ReplayJudge, verify

result = verify(question, query, judge=ReplayJudge.from_directory("tests/verifier/replays"))
result.checks.passed, result.verdict.verdict, result.verdict.codes
```

## Changing the prompt

A change of meaning is a new file, `prompts/verifier-v2.md`, opening with `prompt_version: v2`, and `PROMPT_VERSION` set to `v2`. Replays recorded for v1 are then never served for v2, so the replay tests fail until each replay is reviewed and re-recorded for the new version. A test keeps every model code and verdict named in the prompt.
