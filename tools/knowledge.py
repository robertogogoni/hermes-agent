#!/usr/bin/env python3
"""
tools/knowledge.py — agent tools for the derived Knowledge Layer.

Two tools, registered into the 'ledger' toolset so they are available on EVERY
surface (CLI, gateway/WhatsApp, TUI, WebUI, cron) through the same agent core:

  knowledge_search  — recall promoted facts (subject / type / active filter).
  knowledge_promote — review a pending proposal (approve -> fact, reject -> drop).

These are the supervised bridge between the Interaction Ledger (factual source)
and the derived memory. They mirror interactions_search/interactions_write so
the vocabulary and availability are identical across surfaces.

The leading channel/actor/content/kind arguments exist only to match the
interactions_* tool signature shape; the Knowledge Layer is purely derived, so
they are accepted and ignored (kept for a uniform tool-calling surface).
"""
from __future__ import annotations

import json
from typing import Optional

from tools.registry import registry, tool_result, tool_error

from ledger.knowledge import facts_store
from ledger.knowledge import proposals as _proposals


def knowledge_search(
    subject: Optional[str] = None,
    fact_type: Optional[str] = None,
    active_only: bool = True,
    limit: int = 50,
    *,
    channel: str = "", actor: str = "", content: str = "", kind: str = "",
    hermes_home: Optional[str] = None,
) -> str:
    """Recall promoted knowledge facts. Returns JSON with facts + evidence."""
    try:
        rows = facts_store.search_facts(
            hermes_home=hermes_home,
            subject=subject,
            fact_type=fact_type,
            active_only=active_only,
            limit=limit,
        )
    except Exception as e:  # pragma: no cover - defensive
        return tool_error(f"knowledge_search failed: {e}")
    return tool_result({
        "status": "ok",
        "count": len(rows),
        "facts": [
            {
                "id": r["id"],
                "subject": r["subject"],
                "predicate": r["predicate"],
                "object": r["object"],
                "fact_type": r["fact_type"],
                "valid_from": r["valid_from"],
                "valid_to": r["valid_to"],
                "confidence": r["confidence"],
                "evidence": r.get("evidence", []),
            }
            for r in rows
        ],
    })


def knowledge_promote(
    proposal_id: int,
    decision: str,
    reviewer: str = "human",
    note: Optional[str] = None,
    *,
    channel: str = "", actor: str = "", content: str = "", kind: str = "",
    hermes_home: Optional[str] = None,
) -> str:
    """Review a pending proposal. decision='approve' promotes to a fact;
    decision='reject' discards it. Returns JSON with the new fact_id (or None)."""
    try:
        fid = _proposals.review_proposal(
            hermes_home=hermes_home,
            proposal_id=proposal_id,
            decision=decision,
            reviewer=reviewer,
            note=note,
        )
    except Exception as e:  # pragma: no cover - defensive
        return tool_error(f"knowledge_promote failed: {e}")
    status = "approved" if fid is not None else "rejected"
    return tool_result({"status": status, "proposal_id": proposal_id, "fact_id": fid})


# --- Registry wiring (same shape as interactions_search / interactions_write) ---

KNOWLEDGE_SEARCH_SCHEMA = {
    "name": "knowledge_search",
    "description": (
        "Recall promoted knowledge from the derived memory layer built on the "
        "Interaction Ledger. Returns facts (decisions, procedures, gotchas, "
        "preferences, concepts) each with ledger evidence (interaction row ids) "
        "and validity windows. Filter by subject or fact_type, or pass nothing "
        "to list all active facts. Use this to answer 'what do we know about X' "
        "questions from curated, sourced memory rather than re-reading raw chat."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "Optional subject filter (e.g. 'gateway', 'policy')."},
            "fact_type": {
                "type": "string",
                "description": "Optional type filter: fact | decision | procedure | gotcha | preference | concept.",
            },
            "active_only": {"type": "boolean", "description": "Only return non-superseded facts (default true)."},
            "limit": {"type": "integer", "description": "Max facts to return (default 50)."},
        },
    },
}

KNOWLEDGE_PROMOTE_SCHEMA = {
    "name": "knowledge_promote",
    "description": (
        "Review a pending knowledge proposal. Call knowledge_search or list "
        "pending proposals first to get a proposal_id. decision='approve' "
        "promotes it to a sourced fact (evidence carried over); decision='reject' "
        "discards it. Always supervised: nothing enters promoted memory without "
        "an explicit approve. reviewer labels who approved (e.g. 'rober' or "
        "'auto:policy')."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "proposal_id": {"type": "integer", "description": "The pending proposal id to review."},
            "decision": {
                "type": "string",
                "description": "Either 'approve' (promote to fact) or 'reject' (discard).",
                "enum": ["approve", "reject"],
            },
            "reviewer": {"type": "string", "description": "Who approved/rejected (default 'human')."},
            "note": {"type": "string", "description": "Optional review note / reason."},
        },
        "required": ["proposal_id", "decision"],
    },
}


def _handle_knowledge_search(args, **kw):
    return knowledge_search(
        subject=args.get("subject"),
        fact_type=args.get("fact_type"),
        active_only=args.get("active_only", True),
        limit=args.get("limit", 50),
    )


def _handle_knowledge_promote(args, **kw):
    pid = args.get("proposal_id")
    decision = args.get("decision")
    if pid is None or not decision:
        return tool_error("proposal_id and decision are required")
    return knowledge_promote(
        proposal_id=int(pid),
        decision=decision,
        reviewer=args.get("reviewer", "human"),
        note=args.get("note"),
    )


registry.register(
    name="knowledge_search",
    toolset="ledger",
    schema=KNOWLEDGE_SEARCH_SCHEMA,
    handler=_handle_knowledge_search,
    emoji="🧠",
    max_result_size_chars=6_000,
)

registry.register(
    name="knowledge_promote",
    toolset="ledger",
    schema=KNOWLEDGE_PROMOTE_SCHEMA,
    handler=_handle_knowledge_promote,
    emoji="✅",
    max_result_size_chars=2_000,
)
