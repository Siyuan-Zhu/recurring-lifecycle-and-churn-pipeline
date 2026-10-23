"""Deterministic Synthetic Data Generator for Multi-Rail Recurring Lifecycle Pipeline.

Generates 14 months (2025-01 through 2026-02, plus early 2026-03 clearing window)
of multi-rail commercial subscription and recurring commitment data across:
  - 320 commercial member records (MEM-1001 .. MEM-1320)
  - 240 canonical resolved account entities (ENT-0001 .. ENT-0240)
  - 4 payment rails: Card_Gateway_A, ACH_Bank_Rail, Wire_Portal, Direct_Billing_Subledger
"""

from __future__ import annotations

import calendar
import csv
import datetime as dt
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple


STREET_NAMES: Tuple[str, ...] = (
    "Market", "Embarcadero", "Montgomery", "California", "Kearny", "Battery",
    "Spear", "Howard", "Folsom", "Harrison", "Bryant", "Brannan",
    "Townsend", "King", "Berry", "Illinois", "Third", "Fourth",
    "Lexington", "Madison", "Park", "Vanderbilt", "Broad", "Wall",
    "Water", "Fulton", "Cortlandt", "Clarendon", "Beacon", "Boylston",
    "Tremont", "Congress", "Franklin", "Devonshire", "Summer", "Winter",
    "Wacker", "Michigan", "State", "Dearborn", "Clark", "LaSalle",
)

GENERIC_DOMAINS: Tuple[str, ...] = (
    "gmail.com", "outlook.com", "icloud.com", "yahoo.com", "proton.me"
)

TIER_CONFIG: Tuple[Tuple[str, float], ...] = (
    ("Tier-1 Large", 450.0),
    ("Tier-2 Mid-Market", 180.0),
    ("Tier-3 Commercial", 75.0),
    ("Tier-4 Growth", 35.0),
)


@dataclass(frozen=True)
class SyntheticDataSummary:
    """Summary counts of generated raw CSV datasets and injected anomalies."""

    crm_members: int
    canonical_entities: int
    multi_member_entities: int
    eval_handoff_entities: int
    late_night_utc_shifted_events: int
    split_allocation_dropped_member_txns: int
    split_allocation_subledger_rows: int
    ach_month_end_lag_entities: int
    retry_loop_accounts: int
    retry_loop_raw_rows: int
    gateway_a_rows: int
    ach_bank_rows: int
    subledger_rows: int


def _make_address_pair(idx: int) -> Tuple[str, str]:
    """Return two differently formatted street addresses that normalize identically."""
    street = STREET_NAMES[idx % len(STREET_NAMES)]
    bldg = 100 + (idx * 17) % 890
    suite = 10 + (idx * 7) % 85
    zip_suffix = f"{10 + (idx % 70):02d}"
    addr_a = f"{bldg} {street} Street, Suite {suite}, New York, NY 100{zip_suffix}"
    addr_b = f"{bldg} {street.upper()} ST STE {suite} NEW YORK NY 100{zip_suffix}."
    return addr_a, addr_b


def _month_dates(year: int, month: int, day: int) -> dt.date:
    """Return a valid date in (year, month) clamping day to month-end."""
    max_d = calendar.monthrange(year, month)[1]
    return dt.date(year, month, min(day, max_d))


def _write_csv(path: Path, rows: Sequence[Dict[str, str]]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def generate_all_synthetic_data(base_dir: Path | str | None = None) -> SyntheticDataSummary:
    """Generate all raw CSV datasets deterministically with random.seed(42)."""
    random.seed(42)
    root = Path(base_dir).resolve() if base_dir else Path(__file__).resolve().parent.parent
    raw_dir = root / "data" / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    crm_rows: List[Dict[str, str]] = []
    entity_specs: List[Dict[str, object]] = []
    mem_counter = 1001

    for ent_idx in range(1, 241):
        ent_id = f"ENT-{ent_idx:04d}"
        tier_name, base_niu = TIER_CONFIG[(ent_idx - 1) % len(TIER_CONFIG)]
        niu_amount = round(base_niu + ((ent_idx * 13) % 45), 2)

        if 1 <= ent_idx <= 62:
            is_eval_handoff = ent_idx <= 19
            has_third_member = ent_idx <= 18
            m1_id = f"MEM-{mem_counter:04d}"
            m2_id = f"MEM-{mem_counter + 1:04d}"
            mem_counter += 2
            m3_id = None
            if has_third_member:
                m3_id = f"MEM-{mem_counter:04d}"
                mem_counter += 1

            addr_a, addr_b = _make_address_pair(ent_idx)
            shared_token = f"TOK-SHARED-{ent_idx:04d}-99A"
            shared_domain = f"corp-group-{ent_idx:03d}.io"

            if ent_idx <= 22:
                m1_addr, m2_addr = addr_a, addr_b
                m1_tok = f"TOK-IND-{m1_id}"
                m2_tok = f"TOK-IND-{m2_id}"
                m1_dom = GENERIC_DOMAINS[ent_idx % len(GENERIC_DOMAINS)]
                m2_dom = GENERIC_DOMAINS[(ent_idx + 1) % len(GENERIC_DOMAINS)]
            elif ent_idx <= 42:
                m1_addr = addr_a
                m2_addr = f"{500 + ent_idx} Annex Blvd, Suite {ent_idx}, Boston, MA 02110"
                m1_tok = shared_token
                m2_tok = shared_token
                m1_dom = GENERIC_DOMAINS[ent_idx % len(GENERIC_DOMAINS)]
                m2_dom = GENERIC_DOMAINS[(ent_idx + 2) % len(GENERIC_DOMAINS)]
            else:
                m1_addr = addr_a
                m2_addr = f"{700 + ent_idx} West Loop N, Floor {(ent_idx % 15) + 1}, Chicago, IL 60601"
                m1_tok = f"TOK-IND-{m1_id}"
                m2_tok = f"TOK-IND-{m2_id}"
                m1_dom = shared_domain
                m2_dom = shared_domain

            rail_assigned = "Card_Gateway_A" if ent_idx % 2 == 1 else "Direct_Billing_Subledger"
            crm_rows.append({
                "member_id": m1_id,
                "account_id": f"ACC-{m1_id[4:]}",
                "subscriber_label": f"Subscriber_{m1_id}_Primary",
                "billing_address_raw": m1_addr,
                "payment_token_hash": m1_tok,
                "corporate_parent_domain": m1_dom,
                "assigned_rail": rail_assigned,
                "tier_label": tier_name,
                "contracted_niu": f"{niu_amount:.2f}",
                "onboarding_date": "2024-11-15",
            })
            crm_rows.append({
                "member_id": m2_id,
                "account_id": f"ACC-{m2_id[4:]}",
                "subscriber_label": f"Subscriber_{m2_id}_Successor",
                "billing_address_raw": m2_addr,
                "payment_token_hash": m2_tok,
                "corporate_parent_domain": m2_dom,
                "assigned_rail": rail_assigned,
                "tier_label": tier_name,
                "contracted_niu": f"{niu_amount:.2f}",
                "onboarding_date": "2025-07-20" if not is_eval_handoff else "2026-01-25",
            })

            members_list = [m1_id, m2_id]
            if has_third_member and m3_id is not None:
                crm_rows.append({
                    "member_id": m3_id,
                    "account_id": f"ACC-{m3_id[4:]}",
                    "subscriber_label": f"Subscriber_{m3_id}_Affiliate",
                    "billing_address_raw": f"{900 + ent_idx} Satellite Pkwy, Suite 3, Austin, TX 78701",
                    "payment_token_hash": m2_tok,
                    "corporate_parent_domain": GENERIC_DOMAINS[(ent_idx + 3) % len(GENERIC_DOMAINS)],
                    "assigned_rail": "Card_Gateway_A",
                    "tier_label": tier_name,
                    "contracted_niu": f"{niu_amount:.2f}",
                    "onboarding_date": "2025-06-01",
                })
                members_list.append(m3_id)

            entity_specs.append({
                "expected_entity_id": ent_id,
                "scenario": "EVAL_HANDOFF" if is_eval_handoff else "HIST_HANDOFF",
                "cadence": "monthly",
                "rail": rail_assigned,
                "niu": niu_amount,
                "tier_label": tier_name,
                "members": members_list,
                "billing_day": 10 + (ent_idx % 15),
            })

        else:
            m_id = f"MEM-{mem_counter:04d}"
            mem_counter += 1
            unique_addr = f"{ent_idx * 11} Commerce Way, Suite {ent_idx}, Seattle, WA 98101"
            unique_tok = f"TOK-SOLO-{m_id}"
            unique_dom = f"solo-tenant-{ent_idx:04d}.com"

            if 63 <= ent_idx <= 90:
                scenario = "ACH_MONTH_END_LAG"
                cadence = "monthly"
                rail = "ACH_Bank_Rail"
                billing_day = 28 + (ent_idx % 3)
            elif 91 <= ent_idx <= 106:
                scenario = "QUARTERLY_OR_PREPAID"
                cadence = "quarterly" if ent_idx <= 102 else "semi_annual"
                rail = "Wire_Portal" if ent_idx % 2 == 0 else "Direct_Billing_Subledger"
                billing_day = 15
            elif 107 <= ent_idx <= 127:
                scenario = "TRUE_ACTIONABLE_RISK"
                offset = ent_idx - 107
                if offset < 5:
                    cadence = "weekly"
                elif offset < 10:
                    cadence = "biweekly"
                elif offset < 18:
                    cadence = "monthly"
                else:
                    cadence = "quarterly"
                rail = "Card_Gateway_A" if ent_idx <= 120 else "ACH_Bank_Rail"
                billing_day = 8 + (ent_idx % 12)
            else:
                scenario = "HEALTHY_ACTIVE"
                offset = ent_idx - 128
                if offset < 20:
                    cadence = "weekly"
                elif offset < 46:
                    cadence = "biweekly"
                else:
                    cadence = "monthly"
                rail_choices = (
                    "Card_Gateway_A",
                    "Card_Gateway_A",
                    "ACH_Bank_Rail",
                    "Direct_Billing_Subledger",
                )
                rail = rail_choices[ent_idx % len(rail_choices)]
                billing_day = 5 + (ent_idx % 18)

            crm_rows.append({
                "member_id": m_id,
                "account_id": f"ACC-{m_id[4:]}",
                "subscriber_label": f"Subscriber_{m_id}",
                "billing_address_raw": unique_addr,
                "payment_token_hash": unique_tok,
                "corporate_parent_domain": unique_dom,
                "assigned_rail": rail,
                "tier_label": tier_name,
                "contracted_niu": f"{niu_amount:.2f}",
                "onboarding_date": "2024-10-01",
            })

            entity_specs.append({
                "expected_entity_id": ent_id,
                "scenario": scenario,
                "cadence": cadence,
                "rail": rail,
                "niu": niu_amount,
                "tier_label": tier_name,
                "members": [m_id],
                "billing_day": billing_day,
            })

    months: List[Tuple[int, int]] = [(2025, m) for m in range(1, 13)] + [(2026, 1), (2026, 2)]
    intended_events: List[Dict[str, object]] = []

    def _record_event(
        spec: Dict[str, object],
        member_id: str,
        init_d: dt.date,
        settle_d: dt.date | None = None,
        rail_override: str | None = None,
    ) -> None:
        intended_events.append({
            "entity_id": str(spec["expected_entity_id"]),
            "member_id": member_id,
            "rail": rail_override or str(spec["rail"]),
            "niu": float(spec["niu"]),
            "initiated_date": init_d,
            "settlement_date": settle_d or init_d,
            "scenario": str(spec["scenario"]),
        })

    def _record_periodic_series(
        spec: Dict[str, object],
        member_id: str,
        start_date: dt.date,
        end_date: dt.date,
        step_days: int,
    ) -> None:
        cur = start_date
        while cur <= end_date:
            _record_event(spec, member_id, cur)
            cur += dt.timedelta(days=step_days)

    for spec in entity_specs:
        ent_id = str(spec["expected_entity_id"])
        scenario = str(spec["scenario"])
        cadence = str(spec["cadence"])
        members = list(spec["members"])  # type: ignore[arg-type]
        billing_day = int(spec["billing_day"])  # type: ignore[arg-type]

        if scenario == "EVAL_HANDOFF":
            for yr, mo in months:
                active_mem = members[1] if (yr == 2026 and mo == 2) else members[0]
                _record_event(spec, active_mem, _month_dates(yr, mo, billing_day))

        elif scenario == "HIST_HANDOFF":
            for yr, mo in months:
                active_mem = members[0] if (yr == 2025 and mo <= 7) else members[1]
                _record_event(spec, active_mem, _month_dates(yr, mo, billing_day))

        elif scenario == "ACH_MONTH_END_LAG":
            active_mem = members[0]
            for yr, mo in months:
                if yr == 2026 and mo == 2:
                    init_d = dt.date(2026, 2, 28)
                    lag_days = 2 + (int(ent_id.split("-")[1]) % 3)
                    settle_d = init_d + dt.timedelta(days=lag_days)
                else:
                    init_d = _month_dates(yr, mo, 26)
                    settle_d = _month_dates(yr, mo, 29)
                _record_event(spec, active_mem, init_d, settle_d, rail_override="ACH_Bank_Rail")

        elif scenario == "QUARTERLY_OR_PREPAID":
            active_mem = members[0]
            q_dates = (
                [
                    dt.date(2025, 1, 15),
                    dt.date(2025, 4, 16),
                    dt.date(2025, 7, 16),
                    dt.date(2025, 10, 15),
                    dt.date(2026, 1, 14),
                ]
                if cadence == "quarterly"
                else [
                    dt.date(2025, 1, 15),
                    dt.date(2025, 7, 16),
                    dt.date(2026, 1, 14),
                ]
            )
            for b_date in q_dates:
                _record_event(spec, active_mem, b_date)

        elif scenario == "TRUE_ACTIONABLE_RISK":
            active_mem = members[0]
            if cadence == "weekly":
                _record_periodic_series(spec, active_mem, dt.date(2025, 1, 3), dt.date(2026, 1, 23), 7)
            elif cadence == "biweekly":
                _record_periodic_series(spec, active_mem, dt.date(2025, 1, 6), dt.date(2026, 1, 19), 14)
            elif cadence == "monthly":
                for yr, mo in months[:-1]:
                    _record_event(spec, active_mem, _month_dates(yr, mo, 2))
            else:
                for b_date in (
                    dt.date(2025, 1, 5),
                    dt.date(2025, 4, 6),
                    dt.date(2025, 7, 6),
                    dt.date(2025, 9, 4),
                ):
                    _record_event(spec, active_mem, b_date)

        elif scenario == "HEALTHY_ACTIVE":
            active_mem = members[0]
            if cadence == "weekly":
                _record_periodic_series(spec, active_mem, dt.date(2025, 1, 4), dt.date(2026, 2, 27), 7)
            elif cadence == "biweekly":
                _record_periodic_series(spec, active_mem, dt.date(2025, 1, 7), dt.date(2026, 2, 25), 14)
            else:
                for yr, mo in months:
                    _record_event(spec, active_mem, _month_dates(yr, mo, billing_day))

    member_lookup = {row["member_id"]: row for row in crm_rows}
    card_events_indices = [
        i for i, ev in enumerate(intended_events) if ev["rail"] == "Card_Gateway_A"
    ]
    assert len(card_events_indices) >= 250, f"Expected >=250 card events, got {len(card_events_indices)}"

    late_night_indices = set(card_events_indices[:185])
    split_alloc_indices = set(card_events_indices[185:219])

    gateway_a_rows: List[Dict[str, str]] = []
    ach_bank_rows: List[Dict[str, str]] = []
    subledger_rows: List[Dict[str, str]] = []

    gw_seq = 10001
    ach_seq = 50001
    sub_seq = 80001

    for idx, ev in enumerate(intended_events):
        rail = str(ev["rail"])
        m_id = str(ev["member_id"])
        niu = float(ev["niu"])
        init_d: dt.date = ev["initiated_date"]  # type: ignore[assignment]
        settle_d: dt.date = ev["settlement_date"]  # type: ignore[assignment]
        tok = member_lookup[m_id]["payment_token_hash"]

        if rail == "Card_Gateway_A":
            gw_id = f"GWA-{gw_seq:06d}"
            gw_seq += 1

            if idx in late_night_indices:
                minute = 15 + (idx % 40)
                sec = 10 + (idx % 45)
                eastern_ts = f"{settle_d.isoformat()}T23:{minute:02d}:{sec:02d}-05:00"
                utc_date = settle_d + dt.timedelta(days=1)
                utc_hour = (23 + 5) - 24
                utc_ts = f"{utc_date.isoformat()}T{utc_hour:02d}:{minute:02d}:{sec:02d}Z"
                subledger_booked_date = utc_date.isoformat()
            else:
                hour = 9 + (idx % 10)
                minute = 10 + (idx % 45)
                eastern_ts = f"{settle_d.isoformat()}T{hour:02d}:{minute:02d}:00-05:00"
                utc_ts = f"{settle_d.isoformat()}T{hour + 5:02d}:{minute:02d}:00Z"
                subledger_booked_date = settle_d.isoformat()

            gateway_a_rows.append({
                "gateway_event_id": gw_id,
                "member_id": m_id,
                "payment_token_hash": tok,
                "rail_source": "Card_Gateway_A",
                "event_type": "SETTLED_CHARGE",
                "niu_amount": f"{niu:.2f}",
                "eastern_timestamp": eastern_ts,
                "utc_timestamp": utc_ts,
                "settlement_status": "SETTLED",
            })

            if idx in split_alloc_indices:
                niu_part1 = round(niu * 0.70, 2)
                niu_part2 = round(niu - niu_part1, 2)
                for part_idx, (alloc_code, part_val) in enumerate(
                    (("ALLOC-CORE", niu_part1), ("ALLOC-EXPANSION", niu_part2)), start=1
                ):
                    subledger_rows.append({
                        "subledger_entry_id": f"SUB-{sub_seq:06d}",
                        "source_event_ref": f"{gw_id}-SPLIT-{part_idx}",
                        "parent_gateway_ref": gw_id,
                        "member_id": "",
                        "payment_token_hash": tok,
                        "rail_source": "Card_Gateway_A_Synced",
                        "allocation_bucket": alloc_code,
                        "niu_amount": f"{part_val:.2f}",
                        "subledger_booked_date_utc": subledger_booked_date,
                        "subledger_utc_timestamp": utc_ts,
                        "entry_status": "POSTED_SPLIT",
                    })
                    sub_seq += 1
            else:
                subledger_rows.append({
                    "subledger_entry_id": f"SUB-{sub_seq:06d}",
                    "source_event_ref": gw_id,
                    "parent_gateway_ref": gw_id,
                    "member_id": m_id,
                    "payment_token_hash": tok,
                    "rail_source": "Card_Gateway_A_Synced",
                    "allocation_bucket": "ALLOC-PRIMARY",
                    "niu_amount": f"{niu:.2f}",
                    "subledger_booked_date_utc": subledger_booked_date,
                    "subledger_utc_timestamp": utc_ts,
                    "entry_status": "POSTED",
                })
                sub_seq += 1

        elif rail == "ACH_Bank_Rail":
            ach_id = f"ACH-{ach_seq:06d}"
            ach_seq += 1
            ach_bank_rows.append({
                "ach_settlement_id": ach_id,
                "member_id": m_id,
                "payment_token_hash": tok,
                "rail_source": "ACH_Bank_Rail",
                "initiated_date": init_d.isoformat(),
                "settlement_date": settle_d.isoformat(),
                "niu_amount": f"{niu:.2f}",
                "ach_return_code": "NONE",
                "settlement_status": "CLEARED",
            })
            subledger_rows.append({
                "subledger_entry_id": f"SUB-{sub_seq:06d}",
                "source_event_ref": ach_id,
                "parent_gateway_ref": ach_id,
                "member_id": m_id,
                "payment_token_hash": tok,
                "rail_source": "ACH_Bank_Rail_Synced",
                "allocation_bucket": "ALLOC-PRIMARY",
                "niu_amount": f"{niu:.2f}",
                "subledger_booked_date_utc": settle_d.isoformat(),
                "subledger_utc_timestamp": f"{settle_d.isoformat()}T14:00:00Z",
                "entry_status": "POSTED",
            })
            sub_seq += 1

        else:
            native_ref = f"DIR-{sub_seq:06d}"
            subledger_rows.append({
                "subledger_entry_id": f"SUB-{sub_seq:06d}",
                "source_event_ref": native_ref,
                "parent_gateway_ref": native_ref,
                "member_id": m_id,
                "payment_token_hash": tok,
                "rail_source": rail,
                "allocation_bucket": "ALLOC-PRIMARY",
                "niu_amount": f"{niu:.2f}",
                "subledger_booked_date_utc": settle_d.isoformat(),
                "subledger_utc_timestamp": f"{settle_d.isoformat()}T15:30:00Z",
                "entry_status": "POSTED_NATIVE",
            })
            sub_seq += 1

    retry_loop_specs = [
        s for s in entity_specs if 107 <= int(str(s["expected_entity_id"]).split("-")[1]) <= 120
    ]
    assert len(retry_loop_specs) == 14

    for loop_idx, spec in enumerate(retry_loop_specs, start=1):
        m_id = str(spec["members"][0])  # type: ignore[index]
        niu = float(spec["niu"])
        tok = member_lookup[m_id]["payment_token_hash"]
        base_day = 10 + (loop_idx % 10)
        d0 = dt.date(2026, 2, base_day)
        d1 = d0 + dt.timedelta(days=1)
        d2 = d0 + dt.timedelta(days=2)

        loop_steps = (
            ("RETRY_CHARGE_1", niu, d0, "10:15:00", "PROVISIONAL_ATTEMPT"),
            ("AUTO_REVERSAL_1", -niu, d1, "08:20:00", "REVERSED_INSUFFICIENT"),
            ("RETRY_CHARGE_2", niu, d1, "16:45:00", "PROVISIONAL_ATTEMPT"),
            ("AUTO_REVERSAL_2", -niu, d2, "09:10:00", "REVERSED_FINAL_FAIL"),
        )
        for step_label, signed_niu, step_date, step_time, status_code in loop_steps:
            gw_id = f"GWA-{gw_seq:06d}"
            gw_seq += 1
            gateway_a_rows.append({
                "gateway_event_id": gw_id,
                "member_id": m_id,
                "payment_token_hash": tok,
                "rail_source": "Card_Gateway_A",
                "event_type": step_label,
                "niu_amount": f"{signed_niu:.2f}",
                "eastern_timestamp": f"{step_date.isoformat()}T{step_time}-05:00",
                "utc_timestamp": f"{step_date.isoformat()}T{step_time}Z",
                "settlement_status": status_code,
            })

    _write_csv(raw_dir / "crm_account_directory.csv", crm_rows)
    _write_csv(raw_dir / "gateway_a_events.csv", gateway_a_rows)
    _write_csv(raw_dir / "ach_bank_settlements.csv", ach_bank_rows)
    _write_csv(raw_dir / "subledger_synced_entries.csv", subledger_rows)

    return SyntheticDataSummary(
        crm_members=len(crm_rows),
        canonical_entities=len(entity_specs),
        multi_member_entities=62,
        eval_handoff_entities=19,
        late_night_utc_shifted_events=len(late_night_indices),
        split_allocation_dropped_member_txns=len(split_alloc_indices),
        split_allocation_subledger_rows=len(split_alloc_indices) * 2,
        ach_month_end_lag_entities=28,
        retry_loop_accounts=len(retry_loop_specs),
        retry_loop_raw_rows=len(retry_loop_specs) * 4,
        gateway_a_rows=len(gateway_a_rows),
        ach_bank_rows=len(ach_bank_rows),
        subledger_rows=len(subledger_rows),
    )


if __name__ == "__main__":
    stats = generate_all_synthetic_data()
    print("Generated synthetic datasets:", stats)
