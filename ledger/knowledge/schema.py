#!/usr/bin/env python3
"""
ledger/knowledge/schema.py — schema + versioned migrations for the Knowledge Layer.

The Knowledge Layer is a DERIVED, auditable memory built ON TOP of the
Interaction Ledger (interactions.db). It never replaces the ledger: every
fact/proposal links back to the source interaction rows (evidence) and every
temporal claim keeps valid_from / valid_to so corrections create supersession
rather than overwrite.

Design goals (from the evaluation of external memory systems vs. our Ledger):
  - Provenance first: facts without ledger evidence are rejected by design.
  - Temporal correctness: valid_from / valid_to, learned_at, supersedes.
  - Supervised promotion: proposals are reviewed; only approved rows feed recall.
  - Extensibility: extractors + destinations are pluggable ABCs so future
    needs (Graphiti-style graph, Basic Memory wiki export, embeddings) slot in
    without rewriting the core.
  - Local + private: SQLite only, no external services, no telemetry.

Migrations are ADDTIVE and ordered. Applying is idempotent: a migration whose
version <= current schema_version is skipped. This makes the layer safe across
Hermes updates that might reset the working tree — re-running ensure_schema()
reconciles to the latest version without data loss.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import List, Dict, Any, Optional

# Bump this whenever a new migration is appended to _MIGRATIONS.
SCHEMA_VERSION = 1

# One migration per version. Each entry is (version, sql). The sql is executed
# inside a transaction; schema_version is set to `version` on success.
_MIGRATIONS: List[Dict[str, Any]] = [
    {
        "version": 1,
        "sql": """
CREATE TABLE IF NOT EXISTS knowledge_facts (
  id INTEGER PRIMARY KEY,
  subject TEXT NOT NULL,
  predicate TEXT NOT NULL,
  object TEXT NOT NULL,
  fact_type TEXT NOT NULL DEFAULT 'fact',   -- fact | decision | procedure | gotcha | preference | concept
  valid_from TEXT,                          -- ISO 8601 UTC; when true in the world
  valid_to TEXT,                            -- ISO 8601 UTC or NULL if still valid
  learned_at TEXT NOT NULL,                 -- ISO 8601 UTC; when ingested
  confidence REAL NOT NULL DEFAULT 0.5,     -- 0..1
  status TEXT NOT NULL DEFAULT 'active',    -- active | superseded | deprecated
  supersedes_fact_id INTEGER,               -- FK -> knowledge_facts.id (supersession chain)
  proposal_id INTEGER,                      -- FK -> knowledge_proposals.id
  created_at TEXT NOT NULL,
  FOREIGN KEY (supersedes_fact_id) REFERENCES knowledge_facts(id),
  FOREIGN KEY (proposal_id) REFERENCES knowledge_proposals(id)
);

CREATE TABLE IF NOT EXISTS knowledge_fact_evidence (
  fact_id INTEGER NOT NULL,
  interaction_id INTEGER NOT NULL,          -- FK -> interactions.id in interactions.db
  excerpt TEXT,                             -- short evidence quote
  PRIMARY KEY (fact_id, interaction_id),
  FOREIGN KEY (fact_id) REFERENCES knowledge_facts(id)
);

CREATE TABLE IF NOT EXISTS knowledge_proposals (
  id INTEGER PRIMARY KEY,
  proposal_type TEXT NOT NULL,              -- mirrors fact_type
  subject TEXT NOT NULL,
  predicate TEXT NOT NULL,
  object TEXT NOT NULL,
  valid_from TEXT,
  valid_to TEXT,
  confidence REAL NOT NULL DEFAULT 0.5,
  status TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | rejected | expired
  rationale TEXT,                           -- why the extractor proposed this
  extractor TEXT,                           -- which extractor produced it
  proposed_at TEXT NOT NULL,
  reviewed_at TEXT,
  reviewer TEXT,                             -- 'auto:<policy>' or human label
  review_note TEXT
);

CREATE TABLE IF NOT EXISTS knowledge_proposal_evidence (
  proposal_id INTEGER NOT NULL,
  interaction_id INTEGER NOT NULL,
  excerpt TEXT,
  PRIMARY KEY (proposal_id, interaction_id)
);

CREATE TABLE IF NOT EXISTS knowledge_meta (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE INDEX IF NOT EXISTS idx_facts_type_status ON knowledge_facts(fact_type, status);
CREATE INDEX IF NOT EXISTS idx_facts_valid ON knowledge_facts(valid_from, valid_to);
CREATE INDEX IF NOT EXISTS idx_facts_supersedes ON knowledge_facts(supersedes_fact_id);
CREATE INDEX IF NOT EXISTS idx_evidence_interaction ON knowledge_fact_evidence(interaction_id);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON knowledge_proposals(status);
        """,
    },
]

_LOCK = threading.Lock()
_CONN: Dict[str, sqlite3.Connection] = {}


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


def db_path(hermes_home: Optional[str] = None) -> str:
    """Path to the knowledge database (sits next to interactions.db)."""
    home = _detect_hermes_home(hermes_home)
    return os.path.join(home, "knowledge.db")


def get_connection(hermes_home: Optional[str] = None) -> sqlite3.Connection:
    """Return a process-cached, thread-safe connection to knowledge.db."""
    global _CONN
    home = _detect_hermes_home(hermes_home)
    path = db_path(home)
    with _LOCK:
        if path in _CONN:
            return _CONN[path]
        conn = sqlite3.connect(path, timeout=5.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _CONN[path] = conn
        return conn


def current_schema_version(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "SELECT value FROM knowledge_meta WHERE key='schema_version'"
    ).fetchone()
    if cur is None:
        return 0
    try:
        return int(cur["value"])
    except (TypeError, ValueError):
        return 0


def ensure_schema(hermes_home: Optional[str] = None) -> int:
    """Apply all pending migrations idempotically. Returns the final version."""
    conn = get_connection(hermes_home)
    with _LOCK:
        # Ensure the version table exists before we read it (first run).
        conn.execute(
            "CREATE TABLE IF NOT EXISTS knowledge_meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        conn.commit()
        existing = current_schema_version(conn)
        for mig in _MIGRATIONS:
            v = mig["version"]
            if v <= existing:
                continue
            try:
                conn.executescript(mig["sql"])
                conn.execute(
                    "INSERT INTO knowledge_meta(key, value) VALUES('schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(v),),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
    return current_schema_version(conn)


def reset_for_tests(hermes_home: Optional[str] = None) -> None:
    """Drop all tables and re-create from scratch. Tests only.

    On Windows we cannot delete the open .db file, so we drop tables via SQL
    and clear the connection cache instead of os.remove.
    """
    path = db_path(hermes_home)
    with _LOCK:
        _CONN.pop(path, None)
    conn = get_connection(hermes_home)
    with _LOCK:
        conn.executescript(
            "DROP TABLE IF EXISTS knowledge_facts;"
            "DROP TABLE IF EXISTS knowledge_fact_evidence;"
            "DROP TABLE IF EXISTS knowledge_proposals;"
            "DROP TABLE IF EXISTS knowledge_proposal_evidence;"
            "DROP TABLE IF EXISTS knowledge_meta;"
        )
        conn.commit()
    ensure_schema(hermes_home)
