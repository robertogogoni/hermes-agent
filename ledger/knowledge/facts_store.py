#!/usr/bin/env python3
"""
ledger/knowledge/facts_store.py — insert / supersede / search derived facts.

Facts are the *promoted* memory: only approved proposals become facts, and
every fact MUST cite ledger interaction rows as evidence (provenance-first).
A correction does not overwrite a fact; it supersedes it, preserving the
historical truth and linking old -> new via supersedes_fact_id + valid_to.
"""
from __future__ import annotations

import json
import sqlite3
import datetime
import threading
from typing import List, Dict, Any, Optional, Tuple

from ledger.knowledge import schema

_LOCK = threading.Lock()


def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def insert_fact(
    *,
    hermes_home: Optional[str] = None,
    subject: str,
    predicate: str,
    object: str,
    fact_type: str = "fact",
    valid_from: Optional[str] = None,
    valid_to: Optional[str] = None,
    confidence: float = 0.5,
    evidence: List[Tuple[int, Optional[str]]],
    supersedes_fact_id: Optional[int] = None,
    proposal_id: Optional[int] = None,
) -> Optional[int]:
    """Insert a fact with mandatory ledger evidence. Returns the new fact id.

    Raises ValueError if evidence is empty (provenance is non-negotiable).
    """
    if not evidence:
        raise ValueError("a fact requires at least one evidence (interaction_id) row")

    schema.ensure_schema(hermes_home)
    conn = schema.get_connection(hermes_home)
    ts = _now_utc()
    cur = conn.execute(
        """
        INSERT INTO knowledge_facts
          (subject, predicate, object, fact_type, valid_from, valid_to,
           learned_at, confidence, status, supersedes_fact_id, proposal_id, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            subject, predicate, object, fact_type, valid_from, valid_to,
            ts, confidence, "active", supersedes_fact_id, proposal_id, ts,
        ),
    )
    fid = cur.lastrowid
    conn.executemany(
        "INSERT OR IGNORE INTO knowledge_fact_evidence (fact_id, interaction_id, excerpt) VALUES (?,?,?)",
        [(fid, iid, ex) for (iid, ex) in evidence],
    )

    # If this fact supersedes another, retire the old one (preserve history).
    if supersedes_fact_id is not None:
        conn.execute(
            "UPDATE knowledge_facts SET status='superseded', valid_to=? WHERE id=? AND id<>?",
            (valid_from or ts, supersedes_fact_id, fid),
        )
    conn.commit()
    return fid


def get_fact(hermes_home: Optional[str], fact_id: int) -> Optional[Dict[str, Any]]:
    schema.ensure_schema(hermes_home)
    conn = schema.get_connection(hermes_home)
    row = conn.execute("SELECT * FROM knowledge_facts WHERE id=?", (fact_id,)).fetchone()
    if row is None:
        return None
    ev = conn.execute(
        "SELECT interaction_id, excerpt FROM knowledge_fact_evidence WHERE fact_id=? ORDER BY interaction_id",
        (fact_id,),
    ).fetchall()
    out = dict(row)
    out["evidence"] = [dict(e) for e in ev]
    return out


def search_facts(
    *,
    hermes_home: Optional[str] = None,
    subject: Optional[str] = None,
    fact_type: Optional[str] = None,
    active_only: bool = True,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Search promoted facts. active_only drops superseded/deprecated rows."""
    schema.ensure_schema(hermes_home)
    conn = schema.get_connection(hermes_home)
    where = []
    params: List[Any] = []
    if subject:
        where.append("subject = ?")
        params.append(subject)
    if fact_type:
        where.append("fact_type = ?")
        params.append(fact_type)
    if active_only:
        where.append("status = 'active'")
    sql = "SELECT * FROM knowledge_facts"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY valid_from DESC, id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    result = []
    for r in rows:
        ev = conn.execute(
            "SELECT interaction_id, excerpt FROM knowledge_fact_evidence WHERE fact_id=?",
            (r["id"],),
        ).fetchall()
        out = dict(r)
        out["evidence"] = [dict(e) for e in ev]
        result.append(out)
    return result
