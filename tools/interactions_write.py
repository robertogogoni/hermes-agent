#!/usr/bin/env python3
"""interactions_write — agent tool: append a row to the unified interaction ledger.

Registered into the "ledger" toolset so it is available on every surface
(CLI, gateway/WhatsApp, TUI, WebUI, cron) through the same agent core. The
insert logic lives in ledger/write.py (record_interaction), which is
deliberately safe to call from hot paths: it swallows its own exceptions and
never raises. This tool is the write counterpart to interactions_search and is
used by the digest cron to log a self-audit row after each send (Stage 4.3),
and can be used anywhere an explicit ledger entry is wanted.

Timestamps are written as UTC ISO 8601 by the engine; callers pass the content
and metadata, not the timestamp.
"""
import json

from tools.registry import registry, tool_result, tool_error

INTERACTIONS_WRITE_SCHEMA = {
    "name": "interactions_write",
    "description": (
        "Append one row to the unified interaction ledger (interactions.db, "
        "alongside state.db). Use this to record an explicit entry that the "
        "live writers do not capture automatically — for example, a digest "
        "self-audit row (kind='digest', actor='cron', meta with per-channel "
        "counts). Channel/actor/kind vocabulary matches the ledger: channel in "
        "{whatsapp, cli, cron, webui, subagent}, actor in {user, hermes, cron, "
        "system}, kind in {note, link, command, question, reply, digest, system}. "
        "The timestamp is set automatically to UTC ISO 8601. Returns the new "
        "row id, or an error if the write failed."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "channel": {
                "type": "string",
                "description": "Channel: whatsapp | cli | cron | webui | subagent.",
            },
            "direction": {
                "type": "string",
                "description": "Direction: inbound | outbound. Defaults to 'outbound'.",
            },
            "actor": {
                "type": "string",
                "description": "Actor: user | hermes | cron | system.",
            },
            "content": {
                "type": "string",
                "description": "The text content of the interaction.",
            },
            "kind": {
                "type": "string",
                "description": (
                    "Optional explicit kind override: note | link | command | "
                    "question | reply | digest | system. When omitted, kind is "
                    "auto-classified (link if URLs present, command if it starts "
                    "with a known verb, else note)."
                ),
            },
            "session_id": {
                "type": "string",
                "description": "Optional session id pointer (FK to the session DB).",
            },
            "meta": {
                "type": "object",
                "description": (
                    "Optional JSON object of extra data (e.g. per-channel counts "
                    "for a digest self-audit row)."
                ),
            },
        },
        "required": ["channel", "actor", "content"],
    },
}


def _handle_interactions_write(args, **kw):
    channel = args.get("channel")
    actor = args.get("actor")
    content = args.get("content")
    if not channel or not actor or content is None:
        return tool_error("channel, actor, and content are required")
    direction = args.get("direction") or "outbound"
    session_id = args.get("session_id")
    meta = args.get("meta")
    kind_override = args.get("kind")

    try:
        from ledger.write import record_interaction
    except Exception as e:  # pragma: no cover - module is part of the repo
        return tool_error(f"ledger writer unavailable: {e}")

    try:
        row_id = record_interaction(
            channel=channel,
            direction=direction,
            actor=actor,
            content=content,
            session_id=session_id,
            meta=meta,
            kind=kind_override,
        )
    except Exception as e:  # pragma: no cover - defensive
        return tool_error(f"interactions_write failed: {e}")

    if row_id is None:
        return tool_error("ledger write returned no row id (interactions.db missing or write failed)")
    return tool_result({"row_id": row_id, "status": "written"})


registry.register(
    name="interactions_write",
    toolset="ledger",
    schema=INTERACTIONS_WRITE_SCHEMA,
    handler=_handle_interactions_write,
    emoji="📝",
    max_result_size_chars=4_000,
)
