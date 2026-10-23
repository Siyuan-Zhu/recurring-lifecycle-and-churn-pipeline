"""End-to-End Unit Test Suite for Multi-Rail Recurring Lifecycle & Churn Suppression Pipeline."""

from __future__ import annotations

import contextlib
import json
import pathlib
import sqlite3
import tempfile
import unittest

import openpyxl

from src.cadence_and_false_churn import classify_cadence, evaluate_churn_candidates
from src.entity_resolution import normalize_billing_address, resolve_entities
from src.generate_synthetic_data import generate_all_synthetic_data
from src.outreach_and_redaction import (
    dispatch_outreach_and_redact,
    verify_redaction_compliance,
)
from src.reconcile_rails import (
    normalize_utc_to_eastern_date,
    reconcile_multi_rail_ledger,
)


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestRecurringLifecyclePipeline(unittest.TestCase):
    """Comprehensive verification of entity resolution, rail reconciliation, and churn suppression."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.synth_stats = generate_all_synthetic_data(REPO_ROOT)
        cls.entity_res = resolve_entities(REPO_ROOT / "data" / "raw" / "crm_account_directory.csv")
        cls.recon_stats = reconcile_multi_rail_ledger(
            base_dir=REPO_ROOT,
            entity_result=cls.entity_res,
        )
        cls.churn_stats = evaluate_churn_candidates(base_dir=REPO_ROOT)

    def test_01_synthetic_data_determinism_and_anomalies(self) -> None:
        self.assertEqual(self.synth_stats.crm_members, 320)
        self.assertEqual(self.synth_stats.canonical_entities, 240)
        self.assertEqual(self.synth_stats.multi_member_entities, 62)
        self.assertEqual(self.synth_stats.eval_handoff_entities, 19)
        self.assertEqual(self.synth_stats.late_night_utc_shifted_events, 185)
        self.assertEqual(self.synth_stats.split_allocation_dropped_member_txns, 34)
        self.assertEqual(self.synth_stats.split_allocation_subledger_rows, 68)
        self.assertEqual(self.synth_stats.ach_month_end_lag_entities, 28)
        self.assertEqual(self.synth_stats.retry_loop_accounts, 14)
        self.assertEqual(self.synth_stats.retry_loop_raw_rows, 56)

    def test_02_union_find_entity_resolution_and_in_memory_seam(self) -> None:
        addr1 = "117 Market Street, Suite 17, New York, NY 10011"
        addr2 = "117 MARKET ST STE 17 NEW YORK NY 10011."
        self.assertEqual(normalize_billing_address(addr1), normalize_billing_address(addr2))

        # Test in-memory transitive A-B-C clustering seam without filesystem dependency
        in_memory_rows = [
            {
                "member_id": "MBR-A",
                "billing_address_raw": "500 Pine Street, Suite 20, Seattle, WA 98101",
                "payment_token_hash": "TOK-ALPHA",
                "corporate_parent_domain": "alpha-one.io",
            },
            {
                "member_id": "MBR-B",
                "billing_address_raw": "500 PINE ST STE 20 SEATTLE WA 98101.",
                "payment_token_hash": "TOK-SHARED-BC",
                "corporate_parent_domain": "beta-two.io",
            },
            {
                "member_id": "MBR-C",
                "billing_address_raw": "999 Broadway Ave, Floor 4, Austin, TX 78701",
                "payment_token_hash": "TOK-SHARED-BC",
                "corporate_parent_domain": "gamma-three.io",
            },
        ]
        in_mem_res = resolve_entities(in_memory_rows)
        self.assertEqual(in_mem_res.total_members, 3)
        self.assertEqual(in_mem_res.resolved_entities, 1)
        self.assertEqual(in_mem_res.transitive_three_member_entities, 1)
        self.assertEqual(
            in_mem_res.member_to_entity["MBR-A"],
            in_mem_res.member_to_entity["MBR-C"],
        )

        # Verify full CRM directory resolution
        self.assertEqual(self.entity_res.total_members, 320)
        self.assertEqual(self.entity_res.resolved_entities, 240)
        self.assertEqual(self.entity_res.multi_member_entities, 62)
        self.assertEqual(self.entity_res.transitive_three_member_entities, 18)
        self.assertEqual(self.entity_res.singleton_entities, 178)

    def test_03_multi_rail_reconciliation_and_deduplication(self) -> None:
        # Verify late-night UTC timestamp normalization across midnight boundary
        et_date = normalize_utc_to_eastern_date("2026-03-01T04:35:00Z")
        self.assertEqual(et_date, "2026-02-28")

        same_day_date = normalize_utc_to_eastern_date("2026-02-15T14:30:00Z")
        self.assertEqual(same_day_date, "2026-02-15")

        self.assertEqual(self.recon_stats.canonical_transactions_count, 4786)
        self.assertAlmostEqual(self.recon_stats.canonical_total_niu, 984272.0, places=2)
        self.assertAlmostEqual(self.recon_stats.duplicate_niu_prevented, 843778.0, places=2)
        self.assertEqual(self.recon_stats.utc_shifted_card_txns_resolved, 185)
        self.assertEqual(self.recon_stats.split_allocation_txns_repaired, 34)
        self.assertEqual(self.recon_stats.split_allocation_rows_repaired, 68)
        self.assertEqual(self.recon_stats.retry_reversal_loops_collapsed, 14)
        self.assertEqual(self.recon_stats.retry_reversal_raw_rows_collapsed, 56)
        self.assertEqual(self.recon_stats.ach_month_end_lag_accounts_reconciled, 28)
        self.assertEqual(self.recon_stats.double_counted_niu_remaining, 0.0)

    def test_04_three_stage_false_churn_waterfall(self) -> None:
        self.assertEqual(classify_cadence(7.0), "weekly")
        self.assertEqual(classify_cadence(14.0), "biweekly")
        self.assertEqual(classify_cadence(30.0), "monthly")
        self.assertEqual(classify_cadence(91.0), "quarterly")
        self.assertEqual(classify_cadence(182.0), "semi_annual")

        self.assertEqual(self.churn_stats.naive_churn_alerts, 84)
        self.assertEqual(self.churn_stats.suppressed_stage1_ach_lag, 28)
        self.assertEqual(self.churn_stats.suppressed_stage2_entity_handoff, 19)
        self.assertEqual(self.churn_stats.suppressed_stage3_cadence_alignment, 16)
        self.assertEqual(self.churn_stats.total_false_positives_suppressed, 63)
        self.assertEqual(self.churn_stats.verified_actionable_risks, 21)
        self.assertAlmostEqual(self.churn_stats.false_positive_reduction_pct, 75.0, places=1)
        self.assertAlmostEqual(self.churn_stats.feb_2026_naive_retention_pct, 47.08, places=2)
        self.assertAlmostEqual(self.churn_stats.feb_2026_reconciled_retention_pct, 91.25, places=2)

        # Verify evaluate_churn_candidates also works when passed an open sqlite3.Connection
        db_path = REPO_ROOT / "data" / "processed" / "lifecycle_warehouse.db"
        with contextlib.closing(sqlite3.connect(str(db_path))) as conn:
            conn_report = evaluate_churn_candidates(conn=conn)
        self.assertEqual(conn_report.verified_actionable_risks, 21)
        self.assertEqual(len(conn_report.lifecycle_records), 240)
        self.assertEqual(len(conn_report.cohort_rows), 14)

    def test_05_cooldown_persists_across_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = pathlib.Path(tmp) / "outreach_ledger.json"
            kwargs = {"base_dir": REPO_ROOT, "state_path": ledger}

            # Dry run previews 5 suppressed / 16 new and writes no ledger file.
            dry = dispatch_outreach_and_redact(commit=False, **kwargs)
            self.assertEqual(dry.verified_actionable_risks, 21)
            self.assertEqual((dry.cooldown_suppressed_count, dry.new_outreach_dispatched_count), (5, 16))
            self.assertEqual(dry.outreach_log_total_rows, 5)
            self.assertFalse(ledger.exists())

            # First commit dispatches 16 and persists 5 seeded + 16 new rows.
            first = dispatch_outreach_and_redact(commit=True, **kwargs)
            self.assertEqual((first.cooldown_suppressed_count, first.new_outreach_dispatched_count), (5, 16))
            self.assertEqual(len(json.loads(ledger.read_text())), 21)

            # A second run reads the ledger and dispatches nothing new.
            second = dispatch_outreach_and_redact(commit=True, **kwargs)
            self.assertEqual((second.cooldown_suppressed_count, second.new_outreach_dispatched_count), (21, 0))
            self.assertEqual(second.outreach_log_total_rows, 21)

            # An explicit reset goes back to the seeded state.
            reset = dispatch_outreach_and_redact(commit=True, reset_seed=True, **kwargs)
            self.assertEqual(reset.new_outreach_dispatched_count, 16)

    def test_06_role_based_redaction_and_artifact_integrity(self) -> None:
        brief_path = REPO_ROOT / "output" / "Account_Manager_Action_Brief.md"
        xlsx_path = REPO_ROOT / "output" / "Finance_Ops_Lifecycle_Audit.xlsx"

        self.assertTrue(brief_path.exists())
        self.assertTrue(xlsx_path.exists())

        redaction = verify_redaction_compliance(brief_path)
        self.assertTrue(redaction.redaction_passed)
        self.assertEqual(redaction.niu_leakage_count, 0)
        self.assertEqual(redaction.currency_symbol_count, 0)

        # Verify redaction detector catches deliberate NIU leakage
        with tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8", delete=True) as tmp:
            tmp.write("Leaked financial figure: 1200.00 NIU in field brief.\n")
            tmp.flush()
            failed_check = verify_redaction_compliance(pathlib.Path(tmp.name))
            self.assertFalse(failed_check.redaction_passed)
            self.assertGreater(failed_check.niu_leakage_count, 0)

        wb = openpyxl.load_workbook(xlsx_path)
        expected_sheets = [
            "Executive_Summary_Audit",
            "False_Churn_Waterfall_84",
            "Actionable_Risk_Queue_21",
            "Cohort_Retention_14M",
            "Retry_Loop_Telemetry_14",
            "Entity_Lifecycle_All_240",
        ]
        self.assertEqual(wb.sheetnames, expected_sheets)


if __name__ == "__main__":
    unittest.main()
