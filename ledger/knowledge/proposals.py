#!/usr/bin/env python3
"""
ledger/knowledge/proposals.py — extracted memory candidates + supervised promotion.

An extractor produces a *proposal* (status='pending'). A reviewer (human or an
explicit auto-policy) approves or rejects it. Approval promotes the proposal
into a fact (with its evidence carried over). Rejection discards it without
ever touching the fact table. This keeps the promoted memory auditable: every
fact traces back to a reviewed proposal that traces back to ledger rows.
"""
from __future__ import annotations

import datetime
import threading
from typing import List, Dict, Any, Optional, Tuple

from ledger.knowledge import schema
from ledger.knowledge import facts_store

_LOCK = threading.Lock()


def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_proposal(
    *,
    hermes_home: Optional[str] = None,
    proposal_type: str,
    subject: str,
    predicate: str,
    object: str,
    evidence: List[Tuple[int, Optional[str]]],
    extractor: str = "manual",
    rationale: Optional[str] = None,
    valid_from: Optional[str] = None,
    valid_to: Optional[str] = None,
    confidence: float = 0.5,
) -> int:
    """Create a pending proposal. Returns the new proposal id."""
    schema.ensure_schema(hermes_home)
    conn = schema.get_connection(hermes_home)
    cur = conn.execute(
        """
        INSERT INTO knowledge_proposals
          (proposal_type, subject, predicate, object, valid_from, valid_to,
           confidence, status, rationale, extractor, proposed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            proposal_type, subject, predicate, object, valid_from, valid_to,
            confidence, "pending", rationale, extractor, _now_utc(),
        ),
    )
    pid = cur.lastrowid
    conn.executemany(
        "INSERT OR IGNORE INTO knowledge_proposal_evidence (proposal_id, interaction_id, excerpt) VALUES (?,?,?)",
        [(pid, iid, ex) for (iid, ex) in evidence],
    )
    conn.commit()
    return pid


def list_proposals(
    hermes_home: Optional[str] = None,
    status: Optional[str] = "pending",
) -> List[Dict[str, Any]]:
    schema.ensure_schema(hermes_home)
    conn = schema.get_connection(hermes_home)
    sql = "SELECT * FROM knowledge_proposals"
    params: List[Any] = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY proposed_at DESC, id DESC"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def review_proposal(
    hermes_home: Optional[str],
    proposal_id: int,
    decision: str,  # 'approve' | 'reject'
    reviewer: str = "human",
    note: Optional[str] = None,
) -> Optional[int]:
    """Approve or reject a proposal.

    On 'approve', promotes the proposal to a fact (evidence carried over) and
    returns the new fact id. On 'reject', marks it rejected and returns None.
    """
    decision = decision.lower()
    if decision not in ("approve", "reject"):
        raise ValueError("decision must be 'approve' or 'reject'")

    schema.ensure_schema(hermes_home)
    conn = schema.get_connection(hermes_home)
    row = conn.execute("SELECT * FROM knowledge_proposals WHERE id=?", (proposal_id,)).fetchone()
    if row is None:
        raise ValueError(f"proposal {proposal_id} not found")

    reviewed_at = _now_utc()
    if decision == "reject":
        conn.execute(
            "UPDATE knowledge_proposals SET status='rejected', reviewed_at=?, reviewer=?, review_note=? WHERE id=?",
            (reviewed_at, reviewer, note, proposal_id),
        )
        conn.commit()
        return None

    # Approve -> promote to fact.
    evidence = conn.execute(
        "SELECT interaction_id, excerpt FROM knowledge_proposal_evidence WHERE proposal_id=?",
        (proposal_id,),
    ).fetchall()
    evidence = [(e["interaction_id"], e["excerpt"]) for e in evidence]
    if not evidence:
        # A proposal without evidence cannot become a fact (provenance-first).
        # Reject it instead of silently promoting.
        conn.execute(
            "UPDATE knowledge_proposals SET status='rejected', reviewed_at=?, reviewer=?, review_note=? WHERE id=?",
            (reviewed_at, reviewer, "no evidence; cannot promote", proposal_id),
        )
        conn.commit()
        return None

    fid = facts_store.insert_fact(
        hermes_home=hermes_home,
        subject=row["subject"],
        predicate=row["predicate"],
        object=row["object"],
        fact_type=row["proposal_type"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        confidence=row["confidence"],
        evidence=evidence,
        proposal_id=proposal_id,
    )
    conn.execute(
        "UPDATE knowledge_proposals SET status='approved', reviewed_at=?, reviewer=?, review_note=? WHERE id=?",
        (reviewed_at, reviewer, note, proposal_id),
    )
    conn.commit()
    return fid
