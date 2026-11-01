# Recorded USAspending responses (2026-11-01)

Recorded on 2026-11-01 between 21:29:06 and 21:29:23 UTC, the capture day of `data/snapshot/`, by running `fetch.run` and `golden.run` with a transport that saved every raw response. The tests replay them with no network (`tests/ingest/usaspending_replay.py`). Nothing here is synthetic: rows and values are trimmed, never edited, and the JSON files are only re-indented.

| File | Recorded from | Trimming |
| --- | --- | --- |
| `download_transactions.json` | `POST /api/v2/download/transactions/` | none |
| `download_status_finished.json` | `GET /api/v2/download/status` | none |
| `contracts_prime_transactions_trimmed.csv` | the `Contracts_PrimeTransactions_2026-11-01_H21M24S11_1.csv` member of the generated zip (12,687 rows) | 30 rows kept, unchanged: every transaction of one award with a net-negative obligation, four positive rows of each award type, three negative rows, two rows performed abroad, one row with no competition code, and two rows each of three of the largest recipients; sorted by transaction key |
| `spending_by_transaction_count.json`, `obligations_by_award_category.json`, `agency_awards.json`, `spending_by_category_*.json`, `spending_over_time_month.json` | the endpoints in `index.json` | none (the category requests asked for 5 or 10 rows) |
| `spending_by_geography_pop_state.json` | `POST /api/v2/search/spending_by_geography/` | the 8 largest states of the 56 in the response |
| `index.json` | every recorded method, URL and request body | none |

The tests rebuild the zip in memory from the trimmed CSV under its recorded member name, next to an empty subaward member (the recorded one was empty too). Because the CSV is trimmed, its totals are not the publisher's: the golden test expects a reconciliation mismatch on it, and the reconciliation to the cent is tested on the committed snapshot instead.
