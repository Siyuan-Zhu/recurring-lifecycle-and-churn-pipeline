"""Union-Find (Disjoint-Set) Entity Resolution Module.

Links commercial subscriber records (`member_id`) across three deterministic identity keys:
  1. Normalized billing street address (casing, punctuation, and USPS suffix standardization)
  2. Shared payment token hash (`payment_token_hash`)
  3. Non-generic corporate parent domain (`corporate_parent_domain`)
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Set, Tuple


GENERIC_EMAIL_DOMAINS: Set[str] = {
    "gmail.com",
    "outlook.com",
    "icloud.com",
    "yahoo.com",
    "proton.me",
    "hotmail.com",
    "aol.com",
}

ADDRESS_TOKEN_MAP: Dict[str, str] = {
    "STREET": "ST",
    "SUITE": "STE",
    "AVENUE": "AVE",
    "BOULEVARD": "BLVD",
    "PARKWAY": "PKWY",
    "TERRACE": "TER",
    "FLOOR": "FL",
    "ROAD": "RD",
    "DRIVE": "DR",
}


@dataclass(frozen=True)
class EntityCluster:
    """Canonical resolved commercial entity grouping one or more CRM member records."""

    entity_id: str
    primary_member_id: str
    member_ids: Tuple[str, ...]
    tier_label: str
    contracted_niu: float
    assigned_rail: str
    normalized_address: str
    corporate_parent_domain: str
    linkage_reasons: str

    @property
    def member_count(self) -> int:
        return len(self.member_ids)

    @property
    def member_ids_csv(self) -> str:
        return ",".join(self.member_ids)


@dataclass(frozen=True)
class EntityResolutionResult:
    """Immutable result of Union-Find entity resolution across CRM records."""

    total_members: int
    resolved_entities: int
    multi_member_entities: int
    transitive_three_member_entities: int
    singleton_entities: int
    address_links_detected: int
    token_links_detected: int
    domain_links_detected: int
    clusters: Tuple[EntityCluster, ...]
    member_to_entity: Dict[str, str]
    normalized_addresses: Dict[str, str]
    crm_by_member: Dict[str, Dict[str, str]]


def normalize_billing_address(raw_address: str) -> str:
    """Normalize a postal billing address for deterministic entity matching."""
    cleaned = re.sub(r"[,\.\#\-]", " ", raw_address.upper())
    tokens = [ADDRESS_TOKEN_MAP.get(tok, tok) for tok in cleaned.split()]
    return " ".join(tokens)


class UnionFind:
    """Disjoint-Set (Union-Find) data structure with path compression and union by rank."""

    def __init__(self, elements: Sequence[str]) -> None:
        self.parent: Dict[str, str] = {el: el for el in elements}
        self.rank: Dict[str, int] = {el: 0 for el in elements}
        self.edge_reasons: Dict[Tuple[str, str], Set[str]] = defaultdict(set)

    def find(self, item: str) -> str:
        """Find canonical root representative with path compression."""
        if self.parent[item] != item:
            self.parent[item] = self.find(self.parent[item])
        return self.parent[item]

    def union(self, a: str, b: str, reason: str) -> bool:
        """Union two elements and record linkage telemetry."""
        pair_key = (min(a, b), max(a, b))
        self.edge_reasons[pair_key].add(reason)
        root_a = self.find(a)
        root_b = self.find(b)
        if root_a == root_b:
            return False
        if self.rank[root_a] < self.rank[root_b]:
            self.parent[root_a] = root_b
        elif self.rank[root_a] > self.rank[root_b]:
            self.parent[root_b] = root_a
        else:
            if root_a < root_b:
                self.parent[root_b] = root_a
                self.rank[root_a] += 1
            else:
                self.parent[root_a] = root_b
                self.rank[root_b] += 1
        return True


def _load_crm_rows(crm_source: Path | str | Sequence[Mapping[str, str]]) -> List[Dict[str, str]]:
    """Load CRM rows from a CSV file path or an in-memory sequence of row mappings."""
    if isinstance(crm_source, (Path, str)):
        with Path(crm_source).open("r", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    return [dict(row) for row in crm_source]


def _link_shared_attribute(
    uf: UnionFind,
    attribute_groups: Mapping[str, Sequence[str]],
    reason: str,
) -> int:
    """Union all members sharing an identity attribute and return the link count."""
    links = 0
    for members in attribute_groups.values():
        if len(members) > 1:
            anchor = members[0]
            for other in members[1:]:
                uf.union(anchor, other, reason)
                links += 1
    return links


def resolve_entities(
    crm_source: Path | str | Sequence[Mapping[str, str]],
) -> EntityResolutionResult:
    """Resolve CRM member records into canonical EntityCluster groups via Union-Find."""
    crm_rows = _load_crm_rows(crm_source)
    member_ids = [row["member_id"] for row in crm_rows]
    uf = UnionFind(member_ids)

    by_norm_address: Dict[str, List[str]] = defaultdict(list)
    by_token_hash: Dict[str, List[str]] = defaultdict(list)
    by_corp_domain: Dict[str, List[str]] = defaultdict(list)
    normalized_addresses: Dict[str, str] = {}

    for row in crm_rows:
        m_id = row["member_id"]
        norm_addr = normalize_billing_address(row["billing_address_raw"])
        normalized_addresses[m_id] = norm_addr
        by_norm_address[norm_addr].append(m_id)

        tok = row["payment_token_hash"].strip()
        if tok:
            by_token_hash[tok].append(m_id)

        dom = row["corporate_parent_domain"].strip().lower()
        if dom and dom not in GENERIC_EMAIL_DOMAINS:
            by_corp_domain[dom].append(m_id)

    address_links = _link_shared_attribute(uf, by_norm_address, "SHARED_NORMALIZED_ADDRESS")
    token_links = _link_shared_attribute(uf, by_token_hash, "SHARED_PAYMENT_TOKEN")
    domain_links = _link_shared_attribute(uf, by_corp_domain, "SHARED_CORPORATE_DOMAIN")

    raw_clusters: Dict[str, List[str]] = defaultdict(list)
    for m_id in member_ids:
        raw_clusters[uf.find(m_id)].append(m_id)

    sorted_clusters = sorted(raw_clusters.values(), key=min)

    member_to_entity: Dict[str, str] = {}
    clusters: List[EntityCluster] = []
    crm_by_member: Dict[str, Dict[str, str]] = {r["member_id"]: r for r in crm_rows}

    multi_member_entities = 0
    transitive_three_member_entities = 0

    for idx, cluster_members in enumerate(sorted_clusters, start=1):
        ordered_members = tuple(sorted(cluster_members))
        ent_id = f"ENT-{idx:04d}"
        for m_id in ordered_members:
            member_to_entity[m_id] = ent_id

        if len(ordered_members) > 1:
            multi_member_entities += 1
        if len(ordered_members) >= 3:
            transitive_three_member_entities += 1

        primary_row = crm_by_member[ordered_members[0]]
        reasons: Set[str] = set()
        for i in range(len(ordered_members)):
            for j in range(i + 1, len(ordered_members)):
                pair_key = (ordered_members[i], ordered_members[j])
                reasons.update(uf.edge_reasons.get(pair_key, set()))

        clusters.append(
            EntityCluster(
                entity_id=ent_id,
                primary_member_id=ordered_members[0],
                member_ids=ordered_members,
                tier_label=primary_row.get("tier_label", "UNSPECIFIED"),
                contracted_niu=float(primary_row.get("contracted_niu", 0.0)),
                assigned_rail=primary_row.get("assigned_rail", "UNSPECIFIED"),
                normalized_address=normalized_addresses[ordered_members[0]],
                corporate_parent_domain=primary_row.get("corporate_parent_domain", ""),
                linkage_reasons=",".join(sorted(reasons)) if reasons else "SINGLETON_ENTITY",
            )
        )

    return EntityResolutionResult(
        total_members=len(member_ids),
        resolved_entities=len(clusters),
        multi_member_entities=multi_member_entities,
        transitive_three_member_entities=transitive_three_member_entities,
        singleton_entities=len(clusters) - multi_member_entities,
        address_links_detected=address_links,
        token_links_detected=token_links,
        domain_links_detected=domain_links,
        clusters=tuple(clusters),
        member_to_entity=member_to_entity,
        normalized_addresses=normalized_addresses,
        crm_by_member=crm_by_member,
    )


if __name__ == "__main__":
    base = Path(__file__).resolve().parent.parent
    result = resolve_entities(base / "data" / "raw" / "crm_account_directory.csv")
    print(
        f"Resolved {result.total_members} members into {result.resolved_entities} canonical entities "
        f"({result.multi_member_entities} multi-member clusters, "
        f"{result.transitive_three_member_entities} transitive 3-member clusters)."
    )
