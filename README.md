# recurring-lifecycle-and-churn-pipeline

A Python and SQLite pipeline for recurring-payment data spread over four payment rails. It matches accounts that appear under several member IDs, removes duplicate settlement rows across rails, filters false churn alerts, and keeps a 30-day outreach cooldown. **All data is synthetic.** `src/generate_synthetic_data.py` builds it deterministically: 320 CRM members, 14 months of settlements on four rails, with each anomaly planted on purpose. The pipeline never reads the generator's labels, so the results below test whether it finds what was planted. They are not findings about real customers. Amounts are in index units (NIU).

## Planted anomalies and what the pipeline recovers

| Planted by the generator | Recovered by the pipeline |
| :--- | :--- |
| 320 members that belong to 240 accounts (62 multi-member accounts, 18 of them three-member chains) | 240 accounts, 62 multi-member (Union-Find on address, payment token and parent domain) |
| 185 card payments whose UTC timestamp falls on the next calendar day | 185 matched back to the right day in America/New_York |
| 34 split payments (68 subledger rows) that lost their `member_id` | 34 repaired through `parent_gateway_ref` and `payment_token_hash` |
| 14 accounts with 4-step retry and reversal loops (56 rows) | 14 loops collapsed to zero-net records |
| Subledger rows that mirror card and ACH payments | 3,562 mirror rows removed, 4,786 canonical transactions left, 0.00 NIU double-counted |
| 84 accounts that look churned in 2026-02 (28 ACH month-end lag, 19 billing handoffs inside one account, 16 quarterly or semi-annual accounts, 21 real risks: 14 retry failures and 7 silent stops) | 28 + 19 + 16 suppressed, 21 left as actionable |

The same fixture also gives a 2026-02 retention figure of 47.08% counted by member and 91.25% counted by reconciled account. That gap comes from how the generator splits members across accounts; it is not an estimate of anything real.

## Stages

1. `src/entity_resolution.py`: address normalization and Union-Find clustering.
2. `src/reconcile_rails.py`: timezone fix, split-payment repair, cross-rail deduplication, retry-loop collapse. Writes `data/processed/lifecycle_warehouse.db`.
3. `src/cadence_and_false_churn.py`: median-interval cadence per account (weekly, biweekly, monthly, quarterly, semi-annual). An account is an actionable risk when the time since its last settlement is at least 1.80 times its median interval and none of the three suppression rules apply.
4. `src/outreach_and_redaction.py`: applies the cooldown and writes two files. The finance workbook has the NIU figures. The account-manager brief has none, and a regex check fails the run if "NIU" or a currency symbol appears in it. A sample brief is in [`docs/`](docs/Account_Manager_Action_Brief.example.md).

## The 30-day cooldown persists between runs

Outreach history lives in `data/state/outreach_ledger.json` (gitignored). Each run loads it into the SQLite `outreach_log` table, skips any account contacted in the last 30 days of the evaluation date (2026-02-28), and, on a commit run, writes the new contacts back. The first run seeds the ledger with 5 earlier contacts, so it dispatches 16 accounts and suppresses 5. A second run dispatches 0 and suppresses all 21. `--DryRun` never writes the ledger and `--reset-state` re-seeds it. A test (`test_05_cooldown_persists_across_runs`) covers this.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest
python src/run_pipeline.py                 # generates data, builds the DB, writes output/
python src/run_pipeline.py --reset-state   # start the cooldown ledger over
pytest -q
```

`data/` and `output/` are regenerated and not committed. CI runs ruff, the pipeline and pytest on Python 3.11 to 3.13. MIT licensed.
