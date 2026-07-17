#!/usr/bin/env python3
"""interactions_search — agent tool: query the unified interaction ledger.

Registered into the "ledger" toolset so it is available on every surface
(CLI, gateway/WhatsApp, TUI, WebUI) through the same agent core. The query
engine lives in ledger/interactions_search.py (reused by the Stage 4 digest).

Timezone handling lives in the engine: since/until are ISO 8601; offset-aware
values convert to UTC, naive values are interpreted in the user's configured
IANA timezone (never assumed UTC). An `origin` filter distinguishes live,
replayed, backfilled, and system rows.
"""
import json

from tools.registry import registry, tool_result, tool_error

INTERACTIONS_SEARCH_SCHEMA = {
    "name": "interactions_search",
    "description": (
        "Search the unified interaction ledger: every Roberto<->Hermes exchange "
        "across WhatsApp, CLI, cron, webui, and subagent surfaces, stored in "
        "interactions.db alongside state.db. Use this to answer questions like "
        "'what did I send you yesterday?', 'show links I shared this week', or "
        "'recap my CLI sessions'. Timestamps (ts) are UTC ISO 8601. "
        "Timezone rule: since/until with an offset convert to UTC; naive "
        "timestamps are treated as the user's local timezone (America/Sao_Paulo), "
        "NEVER as UTC — this prevents the 00:39-vs-03:39 class of bug. "
        "Use origin to separate live captures from replayed/backfilled/system rows."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "since": {
                "type": "string",
                "description": (
                    "Start of window, ISO 8601. Offset-aware (e.g. "
                    "'2026-07-16T00:00:00-03:00' or '...Z') converts to UTC. "
                    "Naive (e.g. '2026-07-16T00:00:00') is read as the user's "
                    "local timezone, NOT UTC."
                ),
            },
            "until": {
                "type": "string",
                "description": "End of window, ISO 8601. Same timezone rule as 'since'.",
            },
            "channel": {
                "type": "string",
                "description": "Filter by channel: whatsapp | cli | cron | webui | subagent.",
            },
            "actor": {
                "type": "string",
                "description": "Filter by actor: user | hermes | cron | system.",
            },
            "direction": {
                "type": "string",
                "description": "Filter by direction: inbound | outbound.",
            },
            "kind": {
                "type": "string",
                "description": "Filter by kind: note | link | command | system.",
            },
            "contains": {
                "type": "string",
                "description": "Substring match against message content.",
            },
            "origin": {
                "type": "string",
                "description": (
                    "Filter by capture origin (from meta.origin). One of: "
                    "'live' (gateway/cli captures), 'replayed' (startup-restore "
                    "replay), 'backfilled' (manual backfill of a lost message), "
                    "'system' (incident rows), or any raw origin string. Pass "
                    "'all' or omit for no restriction. May be a list."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Max rows to return (default 100).",
                "default": 100,
            },
            "offset": {
                "type": "integer",
                "description": "Skip first N rows for pagination (default 0).",
                "default": 0,
            },
        },
        "required": [],
    },
}


def _handle_interactions_search(args, **kw):
    try:
        from ledger.interactions_search import interactions_search
    except Exception as e:  # pragma: no cover - module is part of the repo
        return tool_error(f"ledger engine unavailable: {e}")

    try:
        result = interactions_search(
            since=args.get("since"),
            until=args.get("until"),
            channel=args.get("channel"),
            actor=args.get("actor"),
            direction=args.get("direction"),
            kind=args.get("kind"),
            contains=args.get("contains"),
            origin=args.get("origin"),
            limit=args.get("limit", 100),
            offset=args.get("offset", 0),
        )
    except ValueError as e:
        return tool_error(f"bad timestamp: {e}")
    except Exception as e:  # pragma: no cover - defensive
        return tool_error(f"interactions_search failed: {e}")

    if "error" in result and not result.get("rows"):
        return tool_error(result["error"])

    # Shape a compact, model-friendly result.
    rows_out = []
    for r in result["rows"]:
        rows_out.append({
            "id": r["id"],
            "ts": r["ts"],
            "channel": r["channel"],
            "direction": r["direction"],
            "actor": r["actor"],
            "kind": r["kind"],
            "content": r["content"],
            "urls": r["urls"],
            "origin": (r.get("meta") or {}).get("origin"),
        })
    return tool_result({
        "count": result["count"],
        "applied": result.get("applied", {}),
        "rows": rows_out,
    })


registry.register(
    name="interactions_search",
    toolset="ledger",
    schema=INTERACTIONS_SEARCH_SCHEMA,
    handler=_handle_interactions_search,
    emoji="🗂️",
    max_result_size_chars=120_000,
)
