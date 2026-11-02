# Report: s3b-golden-reproduction

Date: 2026-11-02. Branch: `s3b-golden-reproduction`, from `main` at `9f55f6a`. Not merged; the owner opens the pull request.

## Outcome

Done, with one item only partly met. The compiler, run on the S2 warehouse, reproduces 43 of the 48 golden values: 33 `owner_api` and 10 `derived`. That includes the 12 fiscal months, the top-10 reporting share and the null competition code. One value differs and stays failing. Recipient `Z4XGALW7L9K6` (Weston Solutions) gives -923,852.08 against the publisher's 84,868,589.24, because the catalog's `recipient` dimension is the parent (reporting) UEI and the publisher's is the recipient's own UEI. So 4 of the 5 recipients reproduce, not 5. Four values cannot be expressed in the catalog and are listed with reasons. The warehouse built from `main` with no error. No compiler defect was found. No network call was made.

## Verification

`uv run ruff check . && uv run ruff format --check . && uv run pytest -q && python3 scripts/commit_hygiene.py`, last lines, run on `e495855` before this report was committed:

```text
All checks passed!
68 files already formatted
[...]
517 passed, 1 xfailed, 108 warnings in 10.76s
{
  "commits": 25,
  "findings": [],
  "passed": true
}
```

The one xfail is the Weston recipient, marked strict. The 108 warnings are SQLMesh's pydantic notices, as in S2.

`uv run python -m agentic_bi.reproduce --out data/tmp/reproduction.json`, last lines (exit status 0):

```text
43 of 48 reproduced (10 derived), 1 differ (1 known), 0 errors, 4 not expressible; unexpected: none
report: data/tmp/reproduction.json
```

## Done means, item by item

| Brief item | Status | Where |
| --- | --- | --- |
| Each of the 48 golden ids mapped once, to a structured query or to `not_expressible` with a one-line reason | met | `evals/golden_queries.yaml`: 44 queries, 4 not expressible; the loader refuses a missing, repeated or unknown id |
| The CLI builds into `data/tmp/`, runs every query through the read-only query connection, writes golden, reproduced and difference per id | met | `src/agentic_bi/reproduce/__main__.py`; `tests/reproduce/test_reproduce_cli.py` |
| A pytest test does the same and fails on any difference; amounts and counts to the cent; ratios by a written, justified rule | met | `tests/reproduce/test_reproduce_golden.py`, one test per mapped id; rule in `docs/reproduction.md` and `compare.py` |
| At least 30 reproduced, at least 5 derived, the 12 months, the 5 recipients, the top-10 share, the null competition code | partly met | 43 reproduced, 10 derived, all 12 months, the top-10 share and the null code (-47,387.44); only 4 of the 5 recipients (below) |
| A value that does not match is never remapped; it stays failing with both numbers; the test marks it xfail with the reason | met | Weston keeps its natural query; strict xfail; both numbers in the report and the docs |
| `docs/reproduction.md` explains the mapping, the tolerance rule, the rerun, and the not-expressible table, tagged | met | `docs/reproduction.md` |
| The verification line passes | met | above |

## Reproduction table

| Golden id | Tag | Status | Golden | Reproduced | Difference |
| --- | --- | --- | --- | --- | --- |
| `transactions.count` | owner_api | not expressible | 12687 |  |  |
| `obligations.total` | owner_api | reproduced | 1697318332.65 | 1697318332.65 | 0.00 |
| `obligations.fiscal_month.01` | owner_api | reproduced | 90321565.67 | 90321565.67 | 0.00 |
| `obligations.fiscal_month.02` | owner_api | reproduced | 60384929.67 | 60384929.67 | 0.00 |
| `obligations.fiscal_month.03` | owner_api | reproduced | 127304190.15 | 127304190.15 | 0.00 |
| `obligations.fiscal_month.04` | owner_api | reproduced | 311975420.85 | 311975420.85 | 0.00 |
| `obligations.fiscal_month.05` | owner_api | reproduced | 151198107.99 | 151198107.99 | 0.00 |
| `obligations.fiscal_month.06` | owner_api | reproduced | 66843656.91 | 66843656.91 | 0.00 |
| `obligations.fiscal_month.07` | owner_api | reproduced | 80816007.20 | 80816007.20 | 0.00 |
| `obligations.fiscal_month.08` | owner_api | reproduced | 79344605.92 | 79344605.92 | 0.00 |
| `obligations.fiscal_month.09` | owner_api | reproduced | 111430879.28 | 111430879.28 | 0.00 |
| `obligations.fiscal_month.10` | owner_api | reproduced | 138011170.20 | 138011170.20 | 0.00 |
| `obligations.fiscal_month.11` | owner_api | reproduced | 112460065.43 | 112460065.43 | 0.00 |
| `obligations.fiscal_month.12` | owner_api | reproduced | 367227733.38 | 367227733.38 | 0.00 |
| `obligations.recipient.DBD6KDKXWN65` | owner_api | reproduced | 141735682.10 | 141735682.10 | 0.00 |
| `obligations.recipient.VHPJR3MJWV49` | owner_api | reproduced | 136907984.75 | 136907984.75 | 0.00 |
| `obligations.recipient.MHMPN3F73NX3` | owner_api | reproduced | 115370521.15 | 115370521.15 | 0.00 |
| `obligations.recipient.Z4XGALW7L9K6` | owner_api | differs (known) | 84868589.24 | -923852.08 | -85792441.32 |
| `obligations.recipient.DK4TBEJRAR56` | owner_api | reproduced | 66893110.69 | 66893110.69 | 0.00 |
| `obligations.psc.F108` | owner_api | reproduced | 716008251.57 | 716008251.57 | 0.00 |
| `obligations.psc.R499` | owner_api | reproduced | 226716569.88 | 226716569.88 | 0.00 |
| `obligations.psc.R429` | owner_api | reproduced | 101778367.66 | 101778367.66 | 0.00 |
| `obligations.psc.F999` | owner_api | reproduced | 67542562.18 | 67542562.18 | 0.00 |
| `obligations.psc.F115` | owner_api | reproduced | 51595004.66 | 51595004.66 | 0.00 |
| `obligations.naics.562910` | owner_api | reproduced | 768867751.92 | 768867751.92 | 0.00 |
| `obligations.naics.541620` | owner_api | reproduced | 349784383.93 | 349784383.93 | 0.00 |
| `obligations.naics.541611` | owner_api | reproduced | 103690261.13 | 103690261.13 | 0.00 |
| `obligations.naics.541512` | owner_api | reproduced | 58608033.31 | 58608033.31 | 0.00 |
| `obligations.naics.541715` | owner_api | reproduced | 52849733.20 | 52849733.20 | 0.00 |
| `obligations.sub_agency.environmental_protection_agency` | owner_api | reproduced | 1697318332.65 | 1697318332.65 | 0.00 |
| `obligations.pop_state.CA` | owner_api | reproduced | 238749959.14 | 238749959.14 | 0.00 |
| `obligations.pop_state.OH` | owner_api | reproduced | 169208815.00 | 169208815.00 | 0.00 |
| `obligations.pop_state.VA` | owner_api | reproduced | 160568186.52 | 160568186.52 | 0.00 |
| `obligations.pop_state.DC` | owner_api | reproduced | 138024824.27 | 138024824.27 | 0.00 |
| `obligations.pop_state.TX` | owner_api | reproduced | 102283702.42 | 102283702.42 | 0.00 |
| `awards.active_in_period.count` | derived | reproduced | 6016 | 6016 | 0 |
| `awards.net_negative.count` | derived | not expressible | 1710 |  |  |
| `obligations.deobligations.sum` | derived | not expressible | -276256715.93 |  |  |
| `awards.average_net_obligation` | derived | reproduced | 282134.03 | 282134.03135804523 | 0.00135804523 |
| `recipients.top10_reporting_share` | derived | reproduced | 0.449020 | 0.4490202395623079 | 0.0000002395623079 |
| `award.most_negative.net_obligation` | derived | not expressible | -4877871.74 |  |  |
| `obligations.extent_competed.A` | derived | reproduced | 936214658.91 | 936214658.91 | 0.00 |
| `obligations.extent_competed.B` | derived | reproduced | 59448927.17 | 59448927.17 | 0.00 |
| `obligations.extent_competed.C` | derived | reproduced | 45698086.33 | 45698086.33 | 0.00 |
| `obligations.extent_competed.D` | derived | reproduced | 628056676.64 | 628056676.64 | 0.00 |
| `obligations.extent_competed.F` | derived | reproduced | 14023227.19 | 14023227.19 | 0.00 |
| `obligations.extent_competed.G` | derived | reproduced | 13924143.85 | 13924143.85 | 0.00 |
| `obligations.extent_competed.unreported` | derived | reproduced | -47387.44 | -47387.44 | 0.00 |

Amounts and counts must be equal. The two ratios pass within half a unit of the golden value's last written place: 0.005 for the average award value and 0.0000005 for the top-10 share.

## Tolerance rule, in short

The rule comes from the metric's kind in the catalog, not from the mapping. `sum` and `count_distinct` must be exactly equal. Ratios (`ratio`, `top_n_share`, `share_where`) must satisfy |reproduced - golden| <= 0.5 * 10^-p, where p is the number of decimal places the golden value is written with. The golden file stores each ratio rounded half-even to p places, so this bound accepts exactly the values that round to the golden one. The golden unit must also match the metric unit, so an amount cannot borrow the ratio tolerance. `docs/reproduction.md` gives the full reasoning.

## Not expressible

| Golden id | Golden | Reason | Disposition |
| --- | --- | --- | --- |
| `transactions.count` | 12687 | The catalog counts awards, not transactions; no metric counts transaction rows. | candidate catalog change |
| `awards.net_negative.count` | 1710 | Counting awards by the sign of their net obligation needs a per-award sum and a condition on it; no metric kind does that. | candidate catalog change |
| `obligations.deobligations.sum` | -276256715.93 | The catalog sums obligations net only; no metric or filter selects the negative transaction amounts. | candidate catalog change |
| `award.most_negative.net_obligation` | -4877871.74 | Finding the award with the lowest net obligation needs an award dimension and an order with a limit; the ADR-0002 query has neither, and awards are not a catalog dimension. | out of scope by design |

Each entry also carries its nearest structured query and the refusal code the compiler gives it. The run checks that refusal every time.

## Compiler findings

None. Every mapped query compiled and ran. The compiler's SQL holds no literal value, and each result matched the golden value or differed for a reason the catalog defines.

The Weston difference is a catalog definition, not a compiler defect. The compiler does what the catalog says: `recipient` is `fct_contract_transaction.reporting_uei`, as ADR-0002 ("Recipient level") requires. Across the year, Weston's transactions record four parents: ML53DYBW2523 with 64,253,671.53, RBRXSHXKFCB5 with 21,588,769.79, Z4XGALW7L9K6 itself with -923,852.08, and XA9SVRU7K217 with -50,000.00. These add up to the publisher's figure. For the other four recipients, the recipient and reporting levels select the same transactions, and a test proves this on every run. For the S4 owner: an agent asked about a recipient by its own UEI gets the parent-level figure. That is correct under the catalog, but the answer should say so.

## What changed

All inside the brief's owned paths.

- `evals/golden_queries.yaml`: the mapping, 48 entries.
- `src/agentic_bi/reproduce/`:
  - `mapping.py`: a strict loader for the golden file and the mapping.
  - `compare.py`: the comparison rule.
  - `run.py`: runs every query through `semantic.execute`, picks out the result row, compares, and writes the report.
  - `__main__.py`: the CLI.
- `tests/reproduce/`: 270 tests in total:
  - the end-to-end golden tests;
  - loader and comparison unit tests (drafted by a subagent and checked by me);
  - failure modes of the run, using hand-made mappings;
  - the CLI's exit statuses 0, 1 and 2;
  - a check that the docs stay in step with the mapping.
- `docs/reproduction.md`: the mapping, the rule, the rerun, the known difference and the not-expressible table.
- `docs/sources.md`: only the two stale lines named in S2 open question 3. The `action_date` row no longer says it feeds `dim_date.date`. The "Missing competition code" point now says S2 resolved it with a null key and no dimension row.

## Review

Before the final push, an independent subagent reviewed the whole diff against the brief and the Code Review Rules. It found nothing that blocks the merge. Its findings and what I did with them:

1. Done means item 4 is only partly met (4 of 5 recipients). Kept as is and reported here; remapping the value to make it pass is forbidden.
2. The docs overstated the refusal check: it only catches a new metric with the exact name the attempt uses. Fixed: the docs and the code comment now state that limit.
3. The CLI paths (`--out`, `--database`, `--mapping`, `--golden`) are not confined to the repository. Kept: the operator types them, they are not outside input, and the defaults stay in `data/tmp/`.
4. In principle, an amount mapped to a ratio metric could borrow the ratio tolerance. Fixed: the golden unit must now match the metric unit, or the value is an error (tested).
5. Two commit subjects are long, and one docs commit uses the scope `reproduce` while also touching `docs/sources.md`. Kept: changing them would mean rewriting pushed history, which AGENTS.md forbids.

## Open questions

1. **Recipient level.** Should the catalog gain a recipient dimension at the recipient's own level, on `fct_contract_transaction.recipient_uei`, so the publisher's recipient ranking can be expressed? Until then, Weston stays a strict xfail.
2. **Candidate catalog changes.** Three values would become expressible with a `transaction_count` metric (a catalog entry only), a count of net-negative awards (a new metric kind) and a de-obligations sum (a new kind or a sign option). The S4 session owns the compiler tonight, so none is built here.
3. **Ranks.** The golden `rank` fields are not checked, because the structured query has no order or limit (S3a open question 2).
4. **Push allow list.** `.claude/settings.json` allows pushes to `s1-` and `s3a-` branches only. The push of `s3b-golden-reproduction` went through in this session anyway. A later unattended session may need an entry for its own branch.
5. **Session setup.** As in S1 and S2, the prompt set the maintainer's git identity in the repository's local config. It also named the branch `s3b-golden-reproduction`, so that branch is used instead of the session's default branch name.

## Blocked

Nothing.
