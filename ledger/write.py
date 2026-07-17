#!/usr/bin/env python3
"""
ledger/write.py — live writer for the unified interaction ledger.

Inserts interactions into interactions.db (alongside state.db in HERMES_HOME).
Designed to be called from the gateway hot path (inbound handle_message and
outbound send) WITHOUT ever breaking message delivery: every public function
swallows its own exceptions and returns None on failure. Calls from the gateway
should treat the return value as best-effort only.

Channel vocabulary matches Stage 1 backfill (sessions.source verbatim):
  whatsapp | cli | cron | webui | subagent
Actor vocabulary (brief): user | hermes | cron
  - inbound user messages -> actor='user' on EVERY channel
  - outbound bot messages -> actor='hermes' (cron-sourced logged as 'cron')

HERMES_HOME resolution: --hermes-home / HERMES_HOME env / auto-detect dir holding
state.db. No hardcoded paths.
"""
import os
import re
import json
import sqlite3
import threading
import datetime
import logging

logger = logging.getLogger("hermes.ledger")

URL_RE = re.compile(r"https?://\S+")

# Command verbs that mark a message as kind='command' when no URL is present.
COMMAND_VERBS = (
    "/", "run", "exec", "git", "pip", "npm", "python", "cd ", "ls ",
    "commit", "push", "pull", "rm ", "mkdir", "cat ", "echo",
)

_LOCK = threading.Lock()
# Cache the resolved DB path + connection per process.
_CONN = None
_DB_PATH = None

SP_TZ = datetime.timezone(datetime.timedelta(hours=-3))  # America/Sao_Paulo


def _detect_hermes_home(explicit=None):
    if explicit:
        return explicit
    if os.environ.get("HERMES_HOME"):
        return os.environ["HERMES_HOME"]
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE")
    if home:
        cand = os.path.join(home, "AppData", "Local", "hermes")
        if os.path.isfile(os.path.join(cand, "state.db")):
            return cand
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        cand = os.path.join(xdg, "hermes")
        if os.path.isfile(os.path.join(cand, "state.db")):
            return cand
    raise RuntimeError("could not auto-detect HERMES_HOME")


def _connect(explicit_home=None):
    global _CONN, _DB_PATH
    if _CONN is not None:
        return _CONN
    home = _detect_hermes_home(explicit_home)
    db = os.path.join(home, "interactions.db")
    if not os.path.isfile(db):
        # Backfill has not been run yet; nothing to write into.
        raise RuntimeError(f"interactions.db not found at {db}")
    _DB_PATH = db
    _CONN = sqlite3.connect(db, timeout=5.0)
    return _CONN


def _classify_kind(content, urls_present):
    if urls_present:
        return "link"
    c = content.strip().lower()
    if c.startswith(COMMAND_VERBS) or any(c.startswith(v) for v in COMMAND_VERBS):
        return "command"
    return "note"


def record_interaction(
    channel,
    direction,
    actor,
    content,
    *,
    session_id=None,
    meta=None,
    hermes_home=None,
):
    """Insert one interaction row. Returns the new row id, or None on failure.

    Always safe to call from the hot path: never raises.
    """
    try:
        content = content or ""
        urls = URL_RE.findall(content)
        urls_json = json.dumps(urls) if urls else None
        kind = _classify_kind(content, bool(urls))
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        meta_json = json.dumps(meta) if meta is not None else None
        with _LOCK:
            conn = _connect(hermes_home)
            cur = conn.execute(
                "INSERT INTO interactions (ts,channel,direction,actor,kind,content,urls,session_id,meta) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (ts, channel, direction, actor, kind, content, urls_json, session_id, meta_json),
            )
            conn.commit()
            return cur.lastrowid
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("[ledger] record_interaction failed (channel=%s): %s", channel, e)
        return None


def now_sao_paulo():
    return datetime.datetime.now(datetime.timezone.utc).astimezone(SP_TZ).strftime("%Y-%m-%d %H:%M:%S %z")
