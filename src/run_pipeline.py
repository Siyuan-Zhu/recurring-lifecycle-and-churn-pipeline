"""End-to-End Orchestrator for Multi-Rail Recurring Lifecycle & Churn Suppression Pipeline."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

# Ensure project root is on sys.path when invoked as `python3 src/run_pipeline.py`
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.cadence_and_false_churn import ChurnSuppressionReport, evaluate_churn_candidates
from src.entity_resolution import EntityResolutionResult, resolve_entities
from src.generate_synthetic_data import SyntheticDataSummary, generate_all_synthetic_data
from src.outreach_and_redaction import OutreachDisposition, dispatch_outreach_and_redact
from src.reconcile_rails import ReconciliationSummary, reconcile_multi_rail_ledger


@dataclass(frozen=True)
class PipelineRunSummary:
    """Aggregated typed summary across all four pipeline stages."""

    synthetic: SyntheticDataSummary
    entity_resolution: EntityResolutionResult
    reconciliation: ReconciliationSummary
    churn_suppression: ChurnSuppressionReport
    outreach_and_redaction: OutreachDisposition


def run_full_pipeline(
    base_dir: Path | str | None = None,
    commit: bool = True,
    reset_state: bool = False,
) -> PipelineRunSummary:
    """Execute all pipeline stages deterministically and return typed results."""
    root = Path(base_dir).resolve() if base_dir else PROJECT_ROOT

    synthetic = generate_all_synthetic_data(root)
    entity_resolution = resolve_entities(root / "data" / "raw" / "crm_account_directory.csv")
    reconciliation = reconcile_multi_rail_ledger(
        base_dir=root,
        entity_result=entity_resolution,
    )
    churn_suppression = evaluate_churn_candidates(base_dir=root)
    outreach_and_redaction = dispatch_outreach_and_redact(base_dir=root, commit=commit, reset_seed=reset_state)

    return PipelineRunSummary(
        synthetic=synthetic,
        entity_resolution=entity_resolution,
        reconciliation=reconciliation,
        churn_suppression=churn_suppression,
        outreach_and_redaction=outreach_and_redaction,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Multi-Rail Recurring Lifecycle & False-Churn Suppression Pipeline."
    )
    parser.add_argument(
        "--DryRun",
        action="store_true",
        help="Preview dispositions without writing the outreach ledger.",
    )
    parser.add_argument("--reset-state", action="store_true", help="Discard the outreach ledger and re-seed it.")
    args = parser.parse_args()

    results = run_full_pipeline(commit=not args.DryRun, reset_state=args.reset_state)
    rec = results.reconciliation
    ch = results.churn_suppression
    out = results.outreach_and_redaction

    print("=" * 78)
    print("MULTI-RAIL RECURRING LIFECYCLE & CHURN SUPPRESSION PIPELINE")
    print("=" * 78)
    print(
        f"1. Entity Resolution       : {rec.total_members} members -> "
        f"{rec.resolved_entities} canonical entities ({rec.multi_member_entities} multi-member groups)"
    )
    print(
        f"2. Cross-Rail Deduplication: {rec.canonical_transactions_count} canonical txns "
        f"({rec.canonical_total_niu:.2f} NIU) | {rec.duplicate_niu_prevented:.2f} duplicate NIU prevented"
    )
    print(
        f"3. Anomaly Resolution      : {rec.utc_shifted_card_txns_resolved} UTC date shifts normalized | "
        f"{rec.split_allocation_txns_repaired} split joins repaired ({rec.split_allocation_rows_repaired} rows) | "
        f"{rec.retry_reversal_loops_collapsed} retry loops collapsed ({rec.retry_reversal_raw_rows_collapsed} rows)"
    )
    print(
        f"4. False-Churn Waterfall   : {ch.naive_churn_alerts} naive alerts -> "
        f"-{ch.suppressed_stage1_ach_lag} ACH lag -> -{ch.suppressed_stage2_entity_handoff} entity handoff -> "
        f"-{ch.suppressed_stage3_cadence_alignment} cadence alignment = "
        f"{ch.verified_actionable_risks} verified risks "
        f"(-{ch.false_positive_reduction_pct}%)"
    )
    print(
        f"5. Cohort Retention (2026-02): {ch.feb_2026_reconciled_retention_pct}% Reconciled Entity Retention "
        f"vs {ch.feb_2026_naive_retention_pct}% Naive Member Retention"
    )
    print(
        f"6. Outreach & Redaction    : {out.new_outreach_dispatched_count} new dispatches, "
        f"{out.cooldown_suppressed_count} cooldown-suppressed | "
        f"Redaction Passed: {out.redaction_passed} ({out.niu_leakage_count} NIU leaks)"
    )
    print("7. Artifacts Generated     :")
    print(f"   - Warehouse DB          : {rec.db_path}")
    print(f"   - Finance Ops Workbook  : {out.finance_ops_workbook_path}")
    print(f"   - Redacted AM Brief     : {out.account_manager_brief_path}")
    print("=" * 78)


if __name__ == "__main__":
    main()
