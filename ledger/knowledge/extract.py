#!/usr/bin/env python3
"""
ledger/knowledge/extract.py — pluggable extractors that turn ledger rows into proposals.

An Extractor is any object with:
    name: str
    extract(hermes_home) -> List[dict]   # each dict is a proposal-shaped record

This keeps the layer extensible: when new memory needs appear, add a new
Extractor (e.g. embeddings-based, LLM-based, Graphiti-style) without touching
the core. run_extractor() is the bridge that persists extracted records as
pending proposals.
"""
from __future__ import annotations

import re
from typing import List, Dict, Any, Optional

from ledger.knowledge import proposals as _proposals


# Default heuristic extractor: find explicit "Decision:" / "We decided" lines
# in outbound hermes replies and turn them into decision proposals. Low-risk,
# supervised (stays pending until reviewed), and easy to extend.
_DECISION_RE = re.compile(
    r"(?:decision|we decided|i decided|resolved to|policy:)\s*[:\-]?\s*(.{8,200})",
    re.IGNORECASE,
)


class Extractor:
    """Base contract. Subclasses override extract()."""

    name = "base"

    def extract(self, hermes_home: Optional[str] = None) -> List[Dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError


class DecisionExtractor(Extractor):
    """Heuristic extractor: outbound hermes replies that state a decision."""

    name = "decision-heuristic"

    def extract(self, hermes_home: Optional[str] = None) -> List[Dict[str, Any]]:
        from ledger.interactions_search import interactions_search

        result = interactions_search(
            actor="hermes",
            direction="outbound",
            limit=2000,
            hermes_home=hermes_home,
            include_content=True,
        )
        out: List[Dict[str, Any]] = []
        for row in result.get("rows", []):
            content = row.get("content") or ""
            m = _DECISION_RE.search(content)
            if not m:
                continue
            decision_text = m.group(1).strip().rstrip(". ")
            out.append({
                "subject": "gateway" if "gateway" in content.lower() else "session",
                "predicate": "decision",
                "object": decision_text,
                "proposal_type": "decision",
                "evidence": [(row["id"], content[:200])],
                "extractor": self.name,
                "rationale": "heuristic match on decision keyword",
                "confidence": 0.6,
            })
        return out


def run_extractor(
    hermes_home: Optional[str],
    extractor: Extractor,
    dry_run: bool = False,
) -> int:
    """Extract records via the extractor and persist them as pending proposals.

    Returns the number of proposals created. With dry_run=True, nothing is
    written (use to preview before promotion).
    """
    records = extractor.extract(hermes_home)
    if dry_run:
        return len(records)
    created = 0
    for rec in records:
        _proposals.create_proposal(
            hermes_home=hermes_home,
            proposal_type=rec.get("proposal_type", "fact"),
            subject=rec["subject"],
            predicate=rec["predicate"],
            object=rec["object"],
            evidence=rec.get("evidence", []),
            extractor=rec.get("extractor", extractor.name),
            rationale=rec.get("rationale"),
            valid_from=rec.get("valid_from"),
            valid_to=rec.get("valid_to"),
            confidence=rec.get("confidence", 0.5),
        )
        created += 1
    return created
