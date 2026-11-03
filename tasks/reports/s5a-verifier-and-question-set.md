# Report: s5a-verifier-and-question-set

Date: 2026-11-02. Branch: `s5a-verifier-and-question-set`, from `main` at `9f55f6a`. Not merged; the owner opens the pull request.

## Outcome

Done. The verifier of ADR-0002 point 8 is built in `src/agentic_bi/verifier/`, and the eval question set is `evals/questions.yaml`. Both are proven offline, with no key and no network call:

- the deterministic checks are tested directly;
- the model path runs through a replay judge serving hand-written verdicts, and through a fake client for the live adapter.

ADR-0006 records the contract and is marked Proposed, for the owner to accept. No dependency was added. `pyproject.toml` and `uv.lock` are untouched.

An independent reviewer read the whole diff against this brief and AGENTS.md. It was a separate subagent that did not write the code. It found nothing blocking. Its findings and what became of each are listed below.

Tests: 281 in `tests/verifier/`; 529 in the whole suite.

## Verification

`uv run ruff check . && uv run ruff format --check . && uv run pytest -q && python3 scripts/commit_hygiene.py`, last lines, run on the branch before this report was committed:

```text
All checks passed!
78 files already formatted
........................................................................ [ 95%]
.........................                                                [100%]
529 passed, 108 warnings in 7.14s
{
  "commits": 27,
  "findings": [],
  "passed": true
}
```

The 108 warnings are pydantic deprecation warnings raised inside the virtual environment by an installed package, not by this diff.

## Done means, item by item

| Brief item | Where it is met and proven |
| --- | --- |
| ADR-0006 records the contract: input, verdicts, typed issue codes, default model, intent not SQL | `docs/decisions/0006-verifier-and-eval-set.md`, points 1 to 10. `test_verifier_docs.py` keeps every verdict and code named in the ADR and in `docs/verifier.md` |
| Deterministic checks before any model call: schema, catalog, slice; a test for each | `checks.py`. `test_checks.py` has a test for each schema failure, each compiler refusal code, the slice (wholly before, wholly after, partly outside) and the provisional top-N rule. `test_replay.py` and `test_cli.py` show that a failed check never calls the judge |
| Prompt is a versioned file; the verdict is parsed against a JSON schema; a malformed verdict is an error | `prompts/verifier-v1.md` opens with `prompt_version: v1`. `VERDICT_JSON_SCHEMA` and `parse_verdict` are in `verdict.py`. `test_verdict.py` covers more than 25 malformed shapes, each raising `MalformedVerdictError`. Two malformed replays and a malformed live answer exit 2 from the command |
| 40 to 60 questions with the coverage asked; expected queries without top_n pass the schema and compile | 59 questions; see the table below. `test_questions.py` checks every minimum, compiles all 34 expected queries that need no top-N, and checks that the 11 top-N queries fail only on `order_by` and `limit` and compile without them |
| Replay judge; tests for a correct query, each issue code on a wrong query, a refusal; replays hand-written and marked | `replay.py`. 15 files in `tests/verifier/replays/`, each `provenance: hand_written` with "not model output" in its note, plus a README. `test_replay.py` covers each of the eight model codes on a deliberately wrong query that passes the checks, two refusals, a correct query and a correct top-N query |
| `python -m agentic_bi.verifier check --question ... --query ...`: deterministic without a key, model verdict with one | `__main__.py`. `test_cli.py` checks five things. (1) Without a key: deterministic only, exit 3 when not judged. (2) With a key and a working client (a fake `anthropic` module): the model verdict, exit 0 or 1. (3) With a key and no client library: a clear error, exit 2. (4) A failed model call: exit 2. (5) The key never appears in the output |
| `evals/README.md`: the fields, the golden_id link, how S5b scores intent and refusals | `evals/README.md`. `test_verifier_docs.py` keeps every field and refusal reason named |
| Verification command | Above |

## The question set

| Item | Count |
| --- | --- |
| Questions | 59: 45 answers, 14 refusals |
| Refusals by reason | 7 out of catalog, 4 outside the slice, 3 ambiguous |
| Top-N, `requires: [top_n]` | 11 |
| Paraphrase pairs | 8 |
| Questions with a golden link | 26, covering 43 of the 48 golden ids |
| Metrics | `obligations` 27, `award_count` 6, `competition_rate` 5, `avg_award_value` 4, `supplier_concentration_top10` 3 |
| Grains | `total` 36, `fiscal_month` 5, `fiscal_quarter` 3, `fiscal_year` 1 |
| Dimensions grouped by | `award_type` 3, `recipient` 3, `psc` 2, `pop_state` 2, `sub_agency` 1, `naics` 1, `competition` 1, `competed` 1 |
| Dimensions filtered on | `pop_state` 4, `award_type` 3, `psc` 1, `naics` 1, `competition` 1, `competed` 1, `recipient` 1, `sub_agency` 0 |
| Operators | `eq` 9, `ne` 1, `in` 1, `not_in` 1 |

### Golden ids left unlinked, and why

- `transactions.count`, `awards.net_negative.count`, `obligations.deobligations.sum` and `award.most_negative.net_obligation`: the catalog cannot express them. Each is an expected refusal (q46 to q49) with a `catalog_candidate` and the golden id in its note.
- `obligations.recipient.Z4XGALW7L9K6`: the publisher ranks recipients by their own UEI, and the catalog by the reporting (parent) UEI. This UEI's transactions report under three parents in the snapshot (ML53DYBW2523, RBRXSHXKFCB5, XA9SVRU7K217), so the catalog cannot reproduce the publisher's figure. See q35.

### Every question

| id | outcome | metric | grain | dimensions | filters | top-N | refusal | golden | paraphrase |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| q01 | answer | `obligations` | `total` | none | none |  |  | 1 | total_obligations |
| q02 | answer | `obligations` | `total` | none | none |  |  | 1 | total_obligations |
| q03 | answer | `obligations` | `fiscal_month` | none | none |  |  | 12 | monthly_obligations |
| q04 | answer | `obligations` | `fiscal_month` | none | none |  |  | 12 | monthly_obligations |
| q05 | answer | `obligations` | `total` | none | none |  |  | 1 |  |
| q06 | answer | `obligations` | `total` | none | none |  |  |  |  |
| q07 | answer | `obligations` | `fiscal_quarter` | none | none |  |  |  |  |
| q08 | answer | `obligations` | `fiscal_year` | none | none |  |  | 1 |  |
| q09 | answer | `obligations` | `total` | award_type | none |  |  |  |  |
| q10 | answer | `obligations` | `total` | none | award_type ne |  |  |  |  |
| q11 | answer | `obligations` | `total` | sub_agency | none |  |  | 1 |  |
| q12 | answer | `obligations` | `total` | competition | none |  |  | 7 |  |
| q13 | answer | `obligations` | `total` | none | competition eq |  |  | 1 |  |
| q14 | answer | `obligations` | `total` | competed | none |  |  |  |  |
| q15 | answer | `obligations` | `total` | none | recipient eq |  |  | 1 |  |
| q16 | answer | `obligations` | `total` | none | psc eq |  |  | 1 |  |
| q17 | answer | `obligations` | `total` | none | naics eq |  |  | 1 |  |
| q18 | answer | `obligations` | `total` | none | pop_state eq |  |  | 1 | california_obligations |
| q19 | answer | `obligations` | `total` | none | pop_state eq |  |  | 1 | california_obligations |
| q20 | answer | `obligations` | `total` | none | pop_state not_in |  |  |  |  |
| q21 | answer | `award_count` | `total` | none | none |  |  | 1 | active_awards |
| q22 | answer | `award_count` | `total` | none | none |  |  | 1 | active_awards |
| q23 | answer | `award_count` | `fiscal_month` | none | award_type in |  |  |  |  |
| q24 | answer | `award_count` | `total` | none | competed eq |  |  |  |  |
| q25 | answer | `avg_award_value` | `total` | none | none |  |  | 1 | average_award |
| q26 | answer | `avg_award_value` | `total` | none | none |  |  | 1 | average_award |
| q27 | answer | `avg_award_value` | `fiscal_quarter` | none | award_type eq |  |  |  |  |
| q28 | answer | `supplier_concentration_top10` | `total` | none | none |  |  | 1 | top10_share |
| q29 | answer | `supplier_concentration_top10` | `total` | none | none |  |  | 1 | top10_share |
| q30 | answer | `supplier_concentration_top10` | `total` | award_type | none |  |  |  |  |
| q31 | answer | `competition_rate` | `total` | none | none |  |  |  | competition_share |
| q32 | answer | `competition_rate` | `total` | none | none |  |  |  | competition_share |
| q33 | answer | `competition_rate` | `total` | none | pop_state eq |  |  |  |  |
| q34 | answer | `competition_rate` | `fiscal_month` | none | none |  |  |  |  |
| q35 | answer | `obligations` | `total` | recipient | none | desc 5 |  | 4 | top5_recipients |
| q36 | answer | `obligations` | `total` | recipient | none | desc 5 |  | 4 | top5_recipients |
| q37 | answer | `obligations` | `total` | psc | none | desc 5 |  | 5 |  |
| q38 | answer | `obligations` | `total` | naics | none | desc 5 |  | 5 |  |
| q39 | answer | `obligations` | `total` | pop_state | none | desc 5 |  | 5 |  |
| q40 | answer | `obligations` | `total` | pop_state | none | asc 5 |  |  |  |
| q41 | answer | `obligations` | `fiscal_month` | none | none | desc 1 |  | 1 |  |
| q42 | answer | `award_count` | `total` | recipient | none | desc 10 |  |  |  |
| q43 | answer | `award_count` | `total` | psc | none | desc 5 |  |  |  |
| q44 | answer | `avg_award_value` | `total` | award_type | none | desc 1 |  |  |  |
| q45 | answer | `competition_rate` | `fiscal_quarter` | none | none | asc 1 |  |  |  |
| q46 | refuse |  |  |  |  |  | out_of_catalog |  |  |
| q47 | refuse |  |  |  |  |  | out_of_catalog |  |  |
| q48 | refuse |  |  |  |  |  | out_of_catalog |  |  |
| q49 | refuse |  |  |  |  |  | out_of_catalog |  |  |
| q50 | refuse |  |  |  |  |  | out_of_catalog |  |  |
| q51 | refuse |  |  |  |  |  | out_of_catalog |  |  |
| q52 | refuse |  |  |  |  |  | out_of_catalog |  |  |
| q53 | refuse |  |  |  |  |  | outside_slice |  |  |
| q54 | refuse |  |  |  |  |  | outside_slice |  |  |
| q55 | refuse |  |  |  |  |  | outside_slice |  |  |
| q56 | refuse |  |  |  |  |  | outside_slice |  |  |
| q57 | refuse |  |  |  |  |  | ambiguous |  |  |
| q58 | refuse |  |  |  |  |  | ambiguous |  |  |
| q59 | refuse |  |  |  |  |  | ambiguous |  |  |

## Issue codes

| Code | Raised by | Verdict |
| --- | --- | --- |
| `invalid_query` | checks: the structured-query schema | `inconsistent` |
| `outside_catalog` | checks: a compiler refusal; the compiler's own code is kept in `refusal_code` | `inconsistent` |
| `time_range_outside_slice` | checks: dates outside 2024-10-01 to 2025-09-30 | `refuse_expected` when wholly outside, `inconsistent` when partly |
| `wrong_metric` | judge | `inconsistent` |
| `wrong_dimension` | judge | `inconsistent` |
| `missing_filter` | judge | `inconsistent` |
| `wrong_filter` | judge | `inconsistent` |
| `wrong_grain` | judge | `inconsistent` |
| `wrong_time_range` | judge | `inconsistent` |
| `wrong_order` | judge | `inconsistent` |
| `should_refuse` | judge | `refuse_expected` |

The default model is `claude-haiku-5-5`. The model id is a setting: `AGENTIC_BI_VERIFIER_MODEL` or `--model`.

## What changed

All changes are inside the brief's owned paths.

- `src/agentic_bi/verifier/`:
  - `checks.py`: the deterministic checks and the provisional top-N rule;
  - `verdict.py`: the verdicts, the codes, the JSON schema, a small validator and the parser;
  - `excerpt.py`: the catalog excerpt, with no table or column name;
  - `prompt.py` and `prompts/verifier-v1.md`: the prompt and the message around it;
  - `judge.py`: the protocol and the model setting;
  - `replay.py` and `live.py`: the replay judge and the live judge;
  - `verify.py`: the orchestration;
  - `questions.py`: the question-set loader and its coverage;
  - `__main__.py`: the command.
- `tests/verifier/`: eight test files and 15 hand-written replays with a README.
- `evals/questions.yaml` and `evals/README.md`.
- `docs/decisions/0006-verifier-and-eval-set.md` and `docs/verifier.md`.

## Independent review

A reviewer subagent read the diff, the brief and AGENTS.md. It did not write the code. It re-checked the golden links and the codes against the committed snapshot. It also fuzzed the checks with 20,000 random queries and found no uncaught exception. It found nothing blocking.

| # | Finding | Outcome |
| --- | --- | --- |
| 1 | The command's "model verdict with a key" path was not exercised | Fixed: `test_cli.py` runs it through a fake `anthropic` module and checks the verdict, the judge, the exit code and that the key is absent |
| 2 | A client error (network, authentication, rate limit) or deeply nested text escaped as a traceback with exit 1, which reads as a verdict | Fixed: both become errors and exit 2. The client error keeps only its type name |
| 3 | A time range partly outside the slice was called `refuse_expected`, though it may be a date error on an answerable question | Fixed: it is now `inconsistent`; only a range wholly outside the slice is `refuse_expected`. The ADR and the guide say why |
| 4 | The prompt's refusal rule echoed the set's own refusal questions, which would flatter the verifier on this set | Fixed: the rule is stated by class. The ADR's Consequences name the remaining risk |
| 5 | Exit 0 covered both "consistent" and "not judged" | Fixed: "not judged" is exit 3 |
| 6 | A blank rationale or explanation was accepted | Fixed: malformed |
| 7 | `max_tokens` 4096 is tight for a model that thinks by default | Raised to 16000. S5b should confirm on live runs |
| 8 | q14's grouping by `competed` returns a third, empty group | A note added |
| 9 | A doc test skipped silently when a file was missing | Removed; `test_verifier_docs.py` checks the files by exact path |
| 10 | Only one consistent replay was tied to its expected query | Fixed: every consistent replay is checked, except the one that tests a null time range on purpose |
| 11 | `questions.py` finds `evals/` and `docs/golden/` relative to the source checkout | Kept: the repository is run from a checkout. A packaged install would need a path argument, which `load_question_set` already takes |
| 12 | The report was not yet on the branch | This file |

## Open points for S5b

1. **The top-N shape.** The 11 top-N questions use `order_by: {field, direction}` and `limit`, the shape of the S4 brief. If S4's amendment to ADR-0002 settles on another shape, the YAML and the provisional rule in `checks.py` must follow it. Once `parse_query` accepts the fields, the provisional rule is never reached and can be deleted.
2. **The live judge is unproven.** All judge verdicts in this branch are hand-written. Before trusting the judge on the agent's queries, S5b should do three things:
   - run it on the 45 expected queries (all should be `consistent`);
   - run it on the replays' wrong queries (each should get its code);
   - try it on refusal questions written after the prompt.
3. **Wiring.** `AnthropicJudge` makes one `messages.create` call with `output_config: {effort: low}` and `max_tokens` 16000. It has been run only against a fake client; the `anthropic` library comes with S4. S5b still has to:
   - pin the model;
   - set the spend cap;
   - confirm the call against the installed library version;
   - consider structured outputs (`output_config.format` with `VERDICT_JSON_SCHEMA`) to cut malformed answers. The strict parser stays either way.
4. **Golden links follow S3b.** `evals/golden_queries.yaml` from S3b is the reproduction of record. Where it disagrees with a link here, S5b corrects the link. The recipient level (q15, q35, q36) is the likeliest place.
5. **Scoring rules.** `evals/README.md` sets the normalisation for exact-match intent accuracy (defaults, null time range equal to the slice, order of filters and dimensions, `in` with one value equal to `eq`). Until top-N is on `main`, top-N questions are reported as skipped. S5b implements both.
6. **Catalog candidates recorded, not built:**
   - `transaction_count`;
   - `net_negative_award_count`;
   - `deobligations`;
   - an award dimension;
   - `new_award_count`.
   Each is an expected refusal until a catalog brief adds it.
7. **The sub-agency filter.** No question filters on `sub_agency`: the slice holds one sub-agency (6800), so a filter adds nothing. Every dimension is grouped by at least once.

## Open questions for the owner

1. Accept ADR-0006 as written, or amend? Above all: the default model (`claude-haiku-5-5`), the partial-slice rule (`inconsistent`, not `refuse_expected`) and exit status 3 for "not judged".
2. The verifier sees the full catalog excerpt, not only the metric in the query, so it can name the right metric when the wrong one is used. The excerpt is small today. If the catalog grows, S5b may want to narrow it.

## Blocked

Nothing.
