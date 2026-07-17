#!/usr/bin/env python3
"""
ledger/interactions_search.py — query engine for the unified interaction ledger.

Used by:
  - the `interactions_search` agent tool (tools/interactions_search.py)
  - Stage 4 digest recap (future)

Timezone contract (kills the 00:39-vs-03:39 bug at the query layer):
  * since/until are ISO 8601 strings.
  * If the string carries an offset (e.g. "2026-07-16T00:39:00-03:00" or
    "...Z"), it is converted to UTC before querying.
  * If the string is naive (no offset), it is interpreted in the user's
    configured IANA timezone (hermes_time.get_timezone(); defaults to
    America/Sao_Paulo). It is NEVER assumed to be UTC.
  * All comparisons are against the `ts` column, which is stored in UTC
    (ISO 8601 "Z").

Origin filter (addition #2):
  * `origin` matches rows whose meta JSON contains origin=<value>.
  * Special values:
      "live"            -> meta.origin is a live capture (gateway_inbound /
                           gateway_outbound / cli_session_end)
      "replayed"        -> meta.origin == 'gateway_startup_restore_replay'
      "backfilled"      -> meta.origin == 'manual_backfill_lost_queued_message'
      "system"          -> actor == 'system' (incident rows)
      "all"/None        -> no origin restriction
  * Multiple origins may be passed as a list; rows matching ANY are returned.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional

try:
    from hermes_time import get_timezone
except Exception:  # pragma: no cover - hermes_time always present in-repo
    get_timezone = None  # type: ignore

# Default IANA zone used when nothing is configured. This is rober's zone and
# matches the digest's America/Sao_Paulo convention, so naive inputs are never
# silently treated as UTC.
DEFAULT_TZ = "America/Sao_Paulo"

# meta.origin values written by the live writers.
LIVE_ORIGINS = (
    "gateway_inbound",
    "gateway_outbound",
    "cli_session_end",
)


def _resolve_tz() -> Any:
    """Return the user's configured ZoneInfo, or DEFAULT_TZ."""
    if get_timezone is not None:
        try:
            tz = get_timezone()
            if tz is not None:
                return tz
        except Exception:
            pass
    from zoneinfo import ZoneInfo
    return ZoneInfo(DEFAULT_TZ)


def _to_utc_iso(value: str) -> str:
    """Convert an ISO 8601 string (offset or naive) to a UTC 'Z' string.

    Naive strings are interpreted in the user's configured timezone.
    """
    s = value.strip()
    if not s:
        raise ValueError("empty timestamp")
    dt = None
    parse_err = None
    # First try the full string (handles 'Z' as UTC in 3.11+).
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as e:
        parse_err = e
        # Fall back: strip a trailing 'Z' (older fromisoformat) and retry.
        if s.endswith("Z"):
            try:
                dt = datetime.fromisoformat(s[:-1])
            except ValueError as e2:
                parse_err = e2
    if dt is None:
        raise ValueError(f"unparseable timestamp {value!r}: {parse_err}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_resolve_tz())
    dt_utc = dt.astimezone(timezone.utc)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _detect_hermes_home(explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit
    if os.environ.get("HERMES_HOME"):
        return os.environ["HERMES_HOME"]
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE")
    if home:
        cand = os.path.join(home, "AppData", "Local", "hermes")
        if os.path.isfile(os.path.join(cand, "interactions.db")):
            return cand
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        cand = os.path.join(xdg, "hermes")
        if os.path.isfile(os.path.join(cand, "interactions.db")):
            return cand
    raise RuntimeError("could not auto-detect HERMES_HOME")


def _origin_predicate(origin: Any) -> Optional[str]:
    """Return a SQL fragment + params for the origin filter, or None."""
    if origin is None:
        return None
    if isinstance(origin, str):
        origins = [origin]
    else:
        origins = list(origin)
    if not origins or origins == ["all"]:
        return None

    clauses = []
    params: List[Any] = []
    for o in origins:
        if o == "live":
            q = " OR ".join("json_extract(meta,'$.origin') = ?" for _ in LIVE_ORIGINS)
            clauses.append(f"({q})")
            params.extend(LIVE_ORIGINS)
        elif o == "replayed":
            clauses.append("json_extract(meta,'$.origin') = ?")
            params.append("gateway_startup_restore_replay")
        elif o == "backfilled":
            clauses.append("json_extract(meta,'$.origin') = ?")
            params.append("manual_backfill_lost_queued_message")
        elif o == "system":
            clauses.append("actor = ?")
            params.append("system")
        else:
            clauses.append("json_extract(meta,'$.origin') = ?")
            params.append(o)
    return (" AND (" + " OR ".join(clauses) + ")", params)


def interactions_search(
    since: Optional[str] = None,
    until: Optional[str] = None,
    channel: Optional[str] = None,
    actor: Optional[str] = None,
    direction: Optional[str] = None,
    kind: Optional[str] = None,
    contains: Optional[str] = None,
    origin: Any = None,
    limit: int = 100,
    offset: int = 0,
    hermes_home: Optional[str] = None,
    include_content: bool = True,
) -> Dict[str, Any]:
    """Query the unified interaction ledger.

    Returns a dict: {rows, count, applied:{since_utc,until_utc,origin,etc}}.
    Timestamps in results are the raw stored UTC 'Z' values.
    """
    home = _detect_hermes_home(hermes_home)
    db = os.path.join(home, "interactions.db")
    if not os.path.isfile(db):
        return {"rows": [], "count": 0, "error": f"interactions.db not found at {db}",
                "applied": {}}

    where: List[str] = []
    params: List[Any] = []
    applied: Dict[str, Any] = {}

    if since:
        since_utc = _to_utc_iso(since)
        where.append("ts >= ?")
        params.append(since_utc)
        applied["since_utc"] = since_utc
    if until:
        until_utc = _to_utc_iso(until)
        where.append("ts <= ?")
        params.append(until_utc)
        applied["until_utc"] = until_utc
    if channel:
        where.append("channel = ?")
        params.append(channel)
    if actor:
        where.append("actor = ?")
        params.append(actor)
    if direction:
        where.append("direction = ?")
        params.append(direction)
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if contains:
        where.append("content LIKE ?")
        params.append(f"%{contains}%")

    origin_sql = _origin_predicate(origin)
    if origin_sql:
        where.append(origin_sql[0].lstrip(" AND "))
        params.extend(origin_sql[1])
        applied["origin"] = origin

    sql = "SELECT id, ts, channel, direction, actor, kind, content, urls, session_id, meta FROM interactions"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts ASC LIMIT ? OFFSET ?"
    params.extend([int(limit), int(offset)])

    conn = sqlite3.connect(db, timeout=5.0)
    try:
        cur = conn.execute(sql, params)
        rows = []
        for r in cur.fetchall():
            row = {
                "id": r[0], "ts": r[1], "channel": r[2], "direction": r[3],
                "actor": r[4], "kind": r[5],
                "urls": json.loads(r[7]) if r[7] else None,
                "session_id": r[8],
                "meta": json.loads(r[9]) if r[9] else None,
            }
            if include_content:
                row["content"] = r[6]
            else:
                row["content"] = (r[6][:80] if r[6] else None)
            rows.append(row)
    finally:
        conn.close()

    return {"rows": rows, "count": len(rows), "applied": applied}
