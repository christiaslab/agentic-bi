# Eval question set

Status: written in S5a, 2026-11-02, under [ADR-0006](../docs/decisions/0006-verifier-and-eval-set.md). The set is `questions.yaml`; S5b runs it. The verifier that judges answers against it is described in [docs/verifier.md](../docs/verifier.md).

The set measures what ADR-0002 says the eval measures: **intent accuracy** (does the agent turn a question into the right structured query?) and **refusal behaviour** (does it refuse what the catalog cannot answer, and only that?). It does not measure SQL; the compiler writes the SQL.

Every question and expected query was written by hand from `catalog/metrics.yaml`, `docs/golden/golden.json` and the committed snapshot. None was produced by a model.

## Fields

The file holds `version: 1` and a list `questions`. Each question has:

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | `q` and two digits, sequential. An id is never reused or renumbered; a retired question is deleted and its id left unused, so results stay comparable across runs. |
| `question` | yes | The question as a person would ask it. Unique in the set. |
| `outcome` | yes | `answer`: the catalog can answer it. `refuse`: it cannot, and the right behaviour is a refusal. |
| `query` | answers only | The expected structured query, in the shape of `docs/catalog.md`. A refusal has none. |
| `refusal_reason` | refusals only | `out_of_catalog` (a measure, breakdown or field the catalog does not have), `outside_slice` (another agency, award type or period than EPA contracts in fiscal year 2025) or `ambiguous` (the catalog cannot settle what is asked: no measure named, or a period with no anchor). |
| `note` | refusals always, answers when useful | Why the outcome is what it is: the rule a reader needs, such as "March is fiscal month 6". |
| `catalog_candidate` | no, refusals only | The catalog addition that would make the question answerable, such as `transaction_count`. A record, not a request: the catalog changes only through its own brief. |
| `requires` | no | `[top_n]` when the expected query uses `order_by` and `limit`. Exactly those questions carry it. |
| `golden_id` | no, answers only | One id, or a list, from `docs/golden/golden.json` that the expected query's result should reproduce. See below. |
| `paraphrase_group` | no | A name shared by questions that ask the same thing in other words. All members have the same expected outcome, query and golden ids. |

`src/agentic_bi/verifier/questions.py` loads the file strictly: an unknown key, a missing field, a refusal with a query, an unknown golden id or a top-N query without `requires: [top_n]` is an error. `tests/verifier/test_questions.py` checks the coverage below and compiles every expected query that needs no top-N.

## How golden_id links to docs/golden/golden.json

`golden_id` names values by their `id` in `docs/golden/golden.json`. It means: run the expected query through the compiler on the S2 warehouse, and the row the question asks about holds this value.

- A single id goes with a one-row answer (`q01` and `obligations.total`).
- A list goes with a grouped answer: each id is the row of its group. For `q03` the twelve ids are the twelve fiscal months, in order. For top-N questions the ids are the rows the ranking should return.
- The value's own `dimensions` field says which row: `{"fiscal_year": 2025, "fiscal_month": 6}`, `{"psc_code": "F108"}`, `{"competition_code": null}` for the row with no code.
- Linking is not proof. S3b's `evals/golden_queries.yaml` is the reproduction of record; where the two disagree, S3b's result wins and the link here is corrected. One known difference is recorded in `q35`: the publisher ranks recipients by their own UEI and the catalog by the reporting (parent) UEI, so the publisher's fourth recipient is not linked.
- Golden values the catalog cannot express (the transaction count, the net-negative award count, the de-obligation sum, the most negative award) appear as expected refusals with a `catalog_candidate` and the golden id in the note, not as `golden_id`.

## Coverage

At the time of writing (the tests enforce the minimums):

| Item | Count |
| --- | --- |
| Questions | 59: 45 answers, 14 refusals |
| Refusals by reason | 7 out of catalog, 4 outside the slice, 3 ambiguous |
| Top-N questions (`requires: [top_n]`) | 11 |
| Paraphrase groups | 8 pairs |
| Questions with a golden link | 26, covering 43 golden ids |
| Metrics | every one; `obligations` 27, `award_count` 6, `competition_rate` 5, `avg_award_value` 4, `supplier_concentration_top10` 3 |
| Grains | every one; `total` 36, `fiscal_month` 5, `fiscal_quarter` 3, `fiscal_year` 1 |
| Dimensions grouped by | every one at least once |
| Operators | every one at least once |

## How S5b scores it

S5b runs the analyst agent on each question and records the agent's structured query, or its refusal. Three scores, reported separately and never blended into one number.

1. **Intent accuracy** on `outcome: answer` questions: the share whose agent query equals the expected query after normalisation. Normalisation fills defaults (`dimensions: []`, `filters: []`, `grain: total`, `time_range: null`), treats a null time range and 2024-10-01 to 2025-09-30 as the same, ignores the order of dimensions and of filters, treats `in` with one value as `eq`, and sorts the values of `in` and `not_in`. Questions marked `requires: [top_n]` are scored only once `order_by` and `limit` are on `main` (S4); until then they are reported as skipped, not as failed. An answer the agent refused counts as a miss and is listed.
2. **Verifier agreement** on the same answers: the share the verifier judges `consistent`. Where exact match fails but the verifier says `consistent`, the pair is listed for the owner: either the expected query is too narrow or the verifier too lenient. The verifier is first run on the expected queries themselves (all should come back `consistent`) and on the hand-written wrong queries of `tests/verifier/replays/` (each should come back with its code); its agreement on the agent's queries is trusted only as far as those runs go.
3. **Refusal behaviour**: on `outcome: refuse` questions, the share the agent refuses (recall); on `outcome: answer` questions, the share it answers rather than refuses (the false-refusal rate is one minus this). Both are broken down by `refusal_reason`. A refusal for a reason the catalog does not support (for example refusing a question about California as "outside the slice") is listed.

Golden values are a fourth, deterministic check, run without a key: for every question with a `golden_id`, the expected query is compiled and run on the warehouse, and the linked rows must equal the golden values under S3b's comparison rule. A paraphrase group whose members get different agent queries is reported as an instability, whatever the scores.

The LLM part runs on manual or scheduled runs only, with a pinned model and a spend cap (plan row S5b); the golden check runs on every pull request.
