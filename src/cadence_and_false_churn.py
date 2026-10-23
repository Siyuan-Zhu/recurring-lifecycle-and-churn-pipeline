"""Cadence-Aware Rhythm Break Detection & 3-Stage False-Churn Suppression Module.

Evaluates resolved commercial entities and member records across the 14-month warehouse:
  1. Inter-Arrival Cadence Classifier: Computes per-entity median inter-arrival days
     (`weekly`, `biweekly`, `monthly`, `quarterly`, `semi_annual`) and flags rhythm breaks >= 1.80x.
  2. 3-Stage False-Churn Filter:
     - Stage 1 (`SUPPRESSED_STAGE_1_ACH_LAG`): Month-end ACH initiations clearing in early March.
     - Stage 2 (`SUPPRESSED_STAGE_2_ENTITY_HANDOFF`): Sibling member within the same entity assumed billing.
     - Stage 3 (`SUPPRESSED_STAGE_3_CADENCE_ALIGNMENT`): Quarterly/semi-annual commitments in an off-month.
"""

from __future__ import annotations

import calendar
import datetime as dt
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple


EVALUATION_DATE = dt.date(2026, 2, 28)
RHYTHM_BREAK_THRESHOLD = 1.80


@dataclass(frozen=True)
class EntityLifecycleRecord:
    """Per-entity cadence profile, false-churn suppression status, and narrative."""

    entity_id: str
    primary_member_id: str
    member_count: int
    tier_label: str
    assigned_rail: str
    contracted_niu: float
    total_14m_settled_niu: float
    inferred_cadence: str
    median_interval_days: float
    last_initiated_date: str
    last_settled_date_through_feb: str
    elapsed_days: int
    rhythm_break_ratio: float
    was_naive_alert: int
    has_retry_loop_failure: int
    lifecycle_status: str
    is_actionable_risk: int
    auto_narrative: str

    def to_db_tuple(self) -> Tuple[object, ...]:
        return (
            self.entity_id,
            self.primary_member_id,
            self.member_count,
            self.tier_label,
            self.assigned_rail,
            self.contracted_niu,
            self.total_14m_settled_niu,
            self.inferred_cadence,
            self.median_interval_days,
            self.last_initiated_date,
            self.last_settled_date_through_feb,
            self.elapsed_days,
            self.rhythm_break_ratio,
            self.was_naive_alert,
            self.has_retry_loop_failure,
            self.lifecycle_status,
            self.is_actionable_risk,
            self.auto_narrative,
        )


@dataclass(frozen=True)
class CohortRetentionRow:
    """Monthly cohort retention comparison between naive member view and reconciled entity view."""

    billing_month: str
    naive_active_members: int
    naive_retention_pct: float
    reconciled_active_entities: int
    reconciled_retention_pct: float
    false_churn_gap_pct: float
    reconciled_effective_niu: float

    def to_db_tuple(self) -> Tuple[object, ...]:
        return (
            self.billing_month,
            self.naive_active_members,
            self.naive_retention_pct,
            self.reconciled_active_entities,
            self.reconciled_retention_pct,
            self.false_churn_gap_pct,
            self.reconciled_effective_niu,
        )


@dataclass(frozen=True)
class ChurnSuppressionReport:
    """Structured report of the 3-stage false-churn waterfall and cohort retention analysis."""

    naive_churn_alerts: int
    suppressed_stage1_ach_lag: int
    suppressed_stage2_entity_handoff: int
    suppressed_stage3_cadence_alignment: int
    total_false_positives_suppressed: int
    verified_actionable_risks: int
    false_positive_reduction_pct: float
    feb_2026_naive_retention_pct: float
    feb_2026_reconciled_retention_pct: float
    lifecycle_records: Tuple[EntityLifecycleRecord, ...]
    cohort_rows: Tuple[CohortRetentionRow, ...]


def classify_cadence(median_days: float) -> str:
    """Classify recurring billing cadence from median inter-arrival interval in days."""
    if median_days <= 9.5:
        return "weekly"
    if median_days <= 19.0:
        return "biweekly"
    if median_days <= 45.0:
        return "monthly"
    if median_days <= 120.0:
        return "quarterly"
    return "semi_annual"


def _month_end_date(year_month: str) -> dt.date:
    """Return the calendar month-end date for a YYYY-MM string."""
    year, month = int(year_month[:4]), int(year_month[5:7])
    last_day = calendar.monthrange(year, month)[1]
    return dt.date(year, month, last_day)


def _persist_lifecycle_tables(
    conn: sqlite3.Connection,
    lifecycle_records: Sequence[EntityLifecycleRecord],
    cohort_rows: Sequence[CohortRetentionRow],
) -> None:
    """Persist entity_lifecycle_status and monthly_cohort_retention tables to SQLite."""
    cur = conn.cursor()
    cur.executescript(
        """
        DROP TABLE IF EXISTS entity_lifecycle_status;
        DROP TABLE IF EXISTS monthly_cohort_retention;

        CREATE TABLE entity_lifecycle_status (
            entity_id TEXT PRIMARY KEY,
            primary_member_id TEXT NOT NULL,
            member_count INTEGER NOT NULL,
            tier_label TEXT NOT NULL,
            assigned_rail TEXT NOT NULL,
            contracted_niu REAL NOT NULL,
            total_14m_settled_niu REAL NOT NULL,
            inferred_cadence TEXT NOT NULL,
            median_interval_days REAL NOT NULL,
            last_initiated_date TEXT NOT NULL,
            last_settled_date_through_feb TEXT NOT NULL,
            elapsed_days INTEGER NOT NULL,
            rhythm_break_ratio REAL NOT NULL,
            was_naive_alert INTEGER NOT NULL,
            has_retry_loop_failure INTEGER NOT NULL,
            lifecycle_status TEXT NOT NULL,
            is_actionable_risk INTEGER NOT NULL,
            auto_narrative TEXT NOT NULL
        );

        CREATE TABLE monthly_cohort_retention (
            billing_month TEXT PRIMARY KEY,
            naive_active_members INTEGER NOT NULL,
            naive_retention_pct REAL NOT NULL,
            reconciled_active_entities INTEGER NOT NULL,
            reconciled_retention_pct REAL NOT NULL,
            false_churn_gap_pct REAL NOT NULL,
            reconciled_effective_niu REAL NOT NULL
        );
        """
    )

    cur.executemany(
        """
        INSERT INTO entity_lifecycle_status VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        [r.to_db_tuple() for r in lifecycle_records],
    )
    cur.executemany(
        "INSERT INTO monthly_cohort_retention VALUES (?, ?, ?, ?, ?, ?, ?)",
        [c.to_db_tuple() for c in cohort_rows],
    )
    conn.commit()


def evaluate_churn_candidates(
    base_dir: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
    evaluation_date: dt.date = EVALUATION_DATE,
) -> ChurnSuppressionReport:
    """Run cadence profiling, 3-stage false-churn suppression, and 14-month cohort matrix build."""
    root = Path(base_dir).resolve() if base_dir else Path(__file__).resolve().parent.parent
    db_path = root / "data" / "processed" / "lifecycle_warehouse.db"

    owns_conn = conn is None
    active_conn = conn if conn is not None else sqlite3.connect(db_path)
    active_conn.row_factory = sqlite3.Row
    eval_month = evaluation_date.strftime("%Y-%m")

    try:
        cur = active_conn.cursor()
        entities = {
            row["entity_id"]: dict(row)
            for row in cur.execute("SELECT * FROM entities ORDER BY entity_id").fetchall()
        }
        members = {
            row["member_id"]: dict(row)
            for row in cur.execute("SELECT * FROM members ORDER BY member_id").fetchall()
        }
        txns = [
            dict(row)
            for row in cur.execute(
                "SELECT * FROM canonical_transactions ORDER BY initiated_date, canonical_txn_id"
            ).fetchall()
        ]
        retry_loops = {
            row["entity_id"]: dict(row)
            for row in cur.execute("SELECT * FROM telemetry_retry_loops").fetchall()
        }

        txns_by_member: Dict[str, List[Dict[str, object]]] = defaultdict(list)
        txns_by_entity: Dict[str, List[Dict[str, object]]] = defaultdict(list)
        for t in txns:
            txns_by_member[str(t["member_id"])].append(t)
            txns_by_entity[str(t["entity_id"])].append(t)

        naive_alert_by_entity: Dict[str, str] = {}
        for m_id, m_info in sorted(members.items()):
            m_txns = txns_by_member.get(m_id, [])
            active_baseline = any(
                "2025-09" <= str(t["settlement_month"]) <= "2026-01" for t in m_txns
            )
            settled_eval_month = sum(
                float(t["niu_amount"])
                for t in m_txns
                if str(t["settlement_month"]) == eval_month
            )
            if active_baseline and settled_eval_month == 0.0:
                naive_alert_by_entity[str(m_info["entity_id"])] = m_id

        lifecycle_records: List[EntityLifecycleRecord] = []
        suppressed_stage1_ach_lag = 0
        suppressed_stage2_handoff = 0
        suppressed_stage3_cadence = 0
        verified_actionable_count = 0

        for ent_id, ent_info in sorted(entities.items()):
            e_txns = txns_by_entity.get(ent_id, [])
            init_dates = sorted({
                dt.date.fromisoformat(str(t["initiated_date"]))
                for t in e_txns
                if dt.date.fromisoformat(str(t["initiated_date"])) <= evaluation_date
            })
            eval_settled_txns = [
                t for t in e_txns if str(t["settlement_month"]) == eval_month
            ]
            eval_effective_txns = [
                t for t in e_txns if str(t["effective_billing_month"]) == eval_month
            ]
            ach_lag_txns = [
                t for t in eval_effective_txns if int(t["is_month_end_ach_lag"]) == 1
            ]

            intervals = [
                (init_dates[i] - init_dates[i - 1]).days
                for i in range(1, len(init_dates))
            ]
            median_interval = float(statistics.median(intervals)) if intervals else 30.0
            inferred_cadence = classify_cadence(median_interval)

            settled_dates_through_eval = sorted({
                dt.date.fromisoformat(str(t["settlement_date"]))
                for t in e_txns
                if dt.date.fromisoformat(str(t["settlement_date"])) <= evaluation_date
            })
            last_settled_date = settled_dates_through_eval[-1]
            last_initiated_date = init_dates[-1]

            elapsed_days_effective = (evaluation_date - last_initiated_date).days
            rhythm_break_ratio = round(elapsed_days_effective / median_interval, 2)

            is_in_naive_alerts = ent_id in naive_alert_by_entity
            has_retry_loop = 1 if ent_id in retry_loops else 0

            if is_in_naive_alerts:
                alert_member = naive_alert_by_entity[ent_id]
                if ach_lag_txns:
                    suppressed_stage1_ach_lag += 1
                    lag_t = ach_lag_txns[0]
                    lag_days = (
                        dt.date.fromisoformat(str(lag_t["settlement_date"]))
                        - dt.date.fromisoformat(str(lag_t["initiated_date"]))
                    ).days
                    lifecycle_status = "SUPPRESSED_STAGE_1_ACH_LAG"
                    is_actionable_risk = 0
                    auto_narrative = (
                        f"SUPPRESSED [Stage-1 ACH Settlement Lag]: Recurring commitment initiated "
                        f"on {lag_t['initiated_date']} cleared ACH bank rail on {lag_t['settlement_date']} "
                        f"(T+{lag_days}d). Zero true attrition."
                    )
                elif eval_settled_txns:
                    suppressed_stage2_handoff += 1
                    new_payer = str(eval_settled_txns[0]["member_id"])
                    lifecycle_status = "SUPPRESSED_STAGE_2_ENTITY_HANDOFF"
                    is_actionable_risk = 0
                    auto_narrative = (
                        f"SUPPRESSED [Stage-2 Intra-Entity Handoff]: Prior payer {alert_member} paused "
                        f"while linked sibling {new_payer} in {ent_id} ({ent_info['linkage_reasons']}) "
                        f"settled 100% of recurring commitment in {eval_month}."
                    )
                elif rhythm_break_ratio < RHYTHM_BREAK_THRESHOLD:
                    suppressed_stage3_cadence += 1
                    lifecycle_status = "SUPPRESSED_STAGE_3_CADENCE_ALIGNMENT"
                    is_actionable_risk = 0
                    auto_narrative = (
                        f"SUPPRESSED [Stage-3 Cadence Alignment]: On-schedule {inferred_cadence} cadence "
                        f"(median interval {median_interval:.0f}d). Elapsed {elapsed_days_effective}d since "
                        f"{last_initiated_date.isoformat()} is {rhythm_break_ratio:.2f}x interval (<1.80x threshold)."
                    )
                else:
                    verified_actionable_count += 1
                    lifecycle_status = "ACTIONABLE_RHYTHM_BREAK_RISK"
                    is_actionable_risk = 1
                    retry_note = (
                        f" Includes collapsed 4-step gateway retry/reversal failure loop in {eval_month}."
                        if has_retry_loop
                        else " Silent billing lapse with no clearing activity across any rail."
                    )
                    auto_narrative = (
                        f"ACTIONABLE RISK [Rhythm Break]: {inferred_cadence.capitalize()} account "
                        f"(median {median_interval:.0f}d) has elapsed {elapsed_days_effective}d since last valid "
                        f"settlement on {last_settled_date.isoformat()} ({rhythm_break_ratio:.2f}x >= 1.80x threshold)."
                        + retry_note
                    )
            else:
                lifecycle_status = "HEALTHY_ACTIVE_COMMITMENT"
                is_actionable_risk = 0
                auto_narrative = (
                    f"ACTIVE [On-Schedule {inferred_cadence.capitalize()}]: Settled on schedule in {eval_month} "
                    f"(elapsed {elapsed_days_effective}d, ratio {rhythm_break_ratio:.2f}x of {median_interval:.0f}d median)."
                )

            total_entity_niu = round(sum(float(t["niu_amount"]) for t in e_txns), 2)

            lifecycle_records.append(
                EntityLifecycleRecord(
                    entity_id=ent_id,
                    primary_member_id=str(ent_info["primary_member_id"]),
                    member_count=int(ent_info["member_count"]),
                    tier_label=str(ent_info["tier_label"]),
                    assigned_rail=str(ent_info["assigned_rail"]),
                    contracted_niu=float(ent_info["contracted_niu"]),
                    total_14m_settled_niu=total_entity_niu,
                    inferred_cadence=inferred_cadence,
                    median_interval_days=median_interval,
                    last_initiated_date=last_initiated_date.isoformat(),
                    last_settled_date_through_feb=last_settled_date.isoformat(),
                    elapsed_days=elapsed_days_effective,
                    rhythm_break_ratio=rhythm_break_ratio,
                    was_naive_alert=1 if is_in_naive_alerts else 0,
                    has_retry_loop_failure=has_retry_loop,
                    lifecycle_status=lifecycle_status,
                    is_actionable_risk=is_actionable_risk,
                    auto_narrative=auto_narrative,
                )
            )

        all_months = [f"2025-{m:02d}" for m in range(1, 13)] + ["2026-01", "2026-02"]
        cohort_rows: List[CohortRetentionRow] = []

        jan_2025_members = {
            str(t["member_id"]) for t in txns if str(t["settlement_month"]) == "2025-01"
        }
        total_canonical_entities = len(entities)

        for ym in all_months:
            naive_active_members = {
                str(t["member_id"])
                for t in txns
                if str(t["settlement_month"]) == ym and str(t["member_id"]) in jan_2025_members
            }
            naive_retention_pct = round(
                100.0 * len(naive_active_members) / len(jan_2025_members), 2
            )

            m_end = _month_end_date(ym)
            reconciled_active_entities = 0
            for e_rec in lifecycle_records:
                e_txns = txns_by_entity[e_rec.entity_id]
                has_effective_in_month = any(
                    str(t["effective_billing_month"]) == ym for t in e_txns
                )
                if has_effective_in_month:
                    reconciled_active_entities += 1
                else:
                    prior_inits = [
                        dt.date.fromisoformat(str(t["initiated_date"]))
                        for t in e_txns
                        if dt.date.fromisoformat(str(t["initiated_date"])) <= m_end
                    ]
                    if prior_inits:
                        days_gap = (m_end - max(prior_inits)).days
                        if days_gap < e_rec.median_interval_days * RHYTHM_BREAK_THRESHOLD:
                            reconciled_active_entities += 1

            reconciled_retention_pct = round(
                100.0 * reconciled_active_entities / total_canonical_entities, 2
            )
            effective_niu = round(
                sum(float(t["niu_amount"]) for t in txns if str(t["effective_billing_month"]) == ym),
                2,
            )

            cohort_rows.append(
                CohortRetentionRow(
                    billing_month=ym,
                    naive_active_members=len(naive_active_members),
                    naive_retention_pct=naive_retention_pct,
                    reconciled_active_entities=reconciled_active_entities,
                    reconciled_retention_pct=reconciled_retention_pct,
                    false_churn_gap_pct=round(reconciled_retention_pct - naive_retention_pct, 2),
                    reconciled_effective_niu=effective_niu,
                )
            )

        _persist_lifecycle_tables(active_conn, lifecycle_records, cohort_rows)

        naive_count = len(naive_alert_by_entity)
        total_suppressed = (
            suppressed_stage1_ach_lag + suppressed_stage2_handoff + suppressed_stage3_cadence
        )
        false_positive_reduction_pct = round(100.0 * total_suppressed / naive_count, 1)
    finally:
        if owns_conn:
            active_conn.close()

    return ChurnSuppressionReport(
        naive_churn_alerts=naive_count,
        suppressed_stage1_ach_lag=suppressed_stage1_ach_lag,
        suppressed_stage2_entity_handoff=suppressed_stage2_handoff,
        suppressed_stage3_cadence_alignment=suppressed_stage3_cadence,
        total_false_positives_suppressed=total_suppressed,
        verified_actionable_risks=verified_actionable_count,
        false_positive_reduction_pct=false_positive_reduction_pct,
        feb_2026_naive_retention_pct=cohort_rows[-1].naive_retention_pct,
        feb_2026_reconciled_retention_pct=cohort_rows[-1].reconciled_retention_pct,
        lifecycle_records=tuple(lifecycle_records),
        cohort_rows=tuple(cohort_rows),
    )


if __name__ == "__main__":
    report = evaluate_churn_candidates()
    print(
        f"Evaluated {report.naive_churn_alerts} naive alerts -> "
        f"{report.verified_actionable_risks} actionable risks "
        f"(-{report.false_positive_reduction_pct}%)."
    )
