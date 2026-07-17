#!/usr/bin/env python3
"""
ledger/knowledge/destinations.py — pluggable export destinations for promoted facts.

A Destination is any object with:
    name: str
    export(hermes_home, out_dir) -> int   # returns number of items written

This is the extension point for "new needs": today we ship a Markdown wiki
(human-readable, Obsidian-friendly, mirrors the Basic Memory idea without
duplicating capture). Tomorrow you can add a Graphiti exporter, an embeddings
store, or a BM wiki sync — each as its own Destination, no core changes.
"""
from __future__ import annotations

import os
from typing import List, Dict, Any, Optional

from ledger.knowledge import facts_store as _facts
from ledger.knowledge import schema as _schema


class Destination:
    name = "base"

    def export(self, hermes_home: Optional[str], out_dir: str) -> int:  # pragma: no cover
        raise NotImplementedError


class MarkdownDestination(Destination):
    """Export active facts to Markdown files, one per fact, with ledger evidence.

    Format is compatible with the Basic Memory grammar (frontmatter + observations
    + relations) so the wiki can later be served by Basic Memory without re-capture.
    """

    name = "markdown-wiki"

    def export(self, hermes_home: Optional[str], out_dir: str) -> int:
        _schema.ensure_schema(hermes_home)
        os.makedirs(out_dir, exist_ok=True)
        facts = _facts.search_facts(hermes_home=hermes_home, active_only=True, limit=10000)
        written = 0
        for f in facts:
            slug = f"fact-{f['id']}-{f['subject']}".replace(" ", "-").lower()
            path = os.path.join(out_dir, f"{slug}.md")
            lines = [
                "---",
                f"title: {f['subject']} {f['predicate']}",
                f"type: {f['fact_type']}",
                f"permalink: {slug}",
                f"fact_id: {f['id']}",
                f"valid_from: {f['valid_from'] or 'unknown'}",
                f"valid_to: {f['valid_to'] or 'present'}",
                "tags: [knowledge, derived]",
                "---",
                "",
                f"# {f['subject']} {f['predicate']} {f['object']}",
                "",
                "## Observations",
                f"- [{f['fact_type']}] {f['object']} (valid {f['valid_from'] or '?'} -> {f['valid_to'] or 'present'})",
                f"- [confidence] {f['confidence']}",
                "",
                "## Evidence (ledger provenance)",
            ]
            for ev in f.get("evidence", []):
                iid = ev.get("interaction_id")
                excerpt = ev.get("excerpt") or ""
                lines.append(f"- interactions.db row {iid}: {excerpt}")
            lines.append("")
            lines.append(f"source: interactions.db (derived fact {f['id']})")
            lines.append("")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
            written += 1
        return written
