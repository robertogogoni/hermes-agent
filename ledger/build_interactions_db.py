#!/usr/bin/env python3
"""
Stage 1: Create interactions.db (exact schema from brief) + backfill from state.db.

Adaptation note (deviation from brief, to be reported at checkpoint):
  The brief assumes a SEPARATE WhatsApp gateway message store plus a separate
  session DB. In this real codebase both live in ONE file: state.db.
    - WhatsApp + CLI + cron + webui messages are all rows in state.db.messages
    - distinguished by sessions.source ('whatsapp' | 'cli' | 'cron' | 'webui' | 'subagent')
    - messages.timestamp is a REAL unix epoch, not an ISO string
  So "walk the gateway store" + "walk the session DB" collapses to: query
  state.db.messages JOIN sessions, derive channel/actor/direction from source+role.

Channel mapping (Stage 2 decision 2: store sessions.source VERBATIM):
  source=='whatsapp' -> channel='whatsapp'
  source=='cli'      -> channel='cli'
  source=='cron'     -> channel='cron'
  source=='webui'    -> channel='webui'
  source=='subagent' -> channel='subagent'
  (no collapsing: cron/webui/subagent keep their own channel value)
Actor mapping (brief enum is {user, hermes, cron}):
  role=='user'                    -> actor='user'       (user activity = actor='user' on ANY channel)
  source=='cron'                  -> actor='cron'
  otherwise (assistant/tool/...)  -> actor='hermes'
Direction:
  actor=='user' -> 'inbound'; otherwise -> 'outbound'
Kind (best-effort at backfill time; live writers refine this):
  urls present         -> 'link'
  role=='user'         -> 'note'
  role in (tool,)      -> 'command'   (tool invocations ~ commands)
  else                 -> 'reply'
URLs: regex https?://\\S+ extracted at insert time into urls JSON array.

Timestamps stored as ISO 8601 UTC (brief requirement).

HERMES_HOME resolution (no hardcoded paths):
  1. --hermes-home CLI arg
  2. HERMES_HOME environment variable
  3. auto-detect: the directory that contains state.db, searched in this order:
       - HERMES_HOME env (already checked above)
       - $HOME/AppData/Local/hermes  (Windows default location)
       - $XDG_DATA_HOME/hermes
       - ./state.db relative to CWD
  state.db and the output interactions.db both live in HERMES_HOME.

Usage:
  python ledger/build_interactions_db.py [--hermes-home DIR] [--state-db PATH]
                                         [--out PATH] [--no-drop]
"""
import sqlite3, re, json, os, sys, datetime, argparse

URL_RE = re.compile(r"https?://\S+")

SCHEMA = """
CREATE TABLE IF NOT EXISTS interactions (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  channel TEXT NOT NULL,
  direction TEXT NOT NULL,
  actor TEXT NOT NULL,
  kind TEXT,
  content TEXT NOT NULL,
  urls TEXT,
  session_id TEXT,
  meta TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS interactions_fts
  USING fts5(content, content='interactions', content_rowid='id');

CREATE TRIGGER IF NOT EXISTS interactions_ai AFTER INSERT ON interactions BEGIN
  INSERT INTO interactions_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS interactions_ad AFTER DELETE ON interactions BEGIN
  INSERT INTO interactions_fts(interactions_fts, rowid, content) VALUES('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS interactions_au AFTER UPDATE ON interactions BEGIN
  INSERT INTO interactions_fts(interactions_fts, rowid, content) VALUES('delete', old.id, old.content);
  INSERT INTO interactions_fts(rowid, content) VALUES (new.id, new.content);
END;
"""

SP_TZ = datetime.timezone(datetime.timedelta(hours=-3))  # America/Sao_Paulo


def detect_hermes_home(explicit=None):
    if explicit:
        return explicit
    if os.environ.get("HERMES_HOME"):
        return os.environ["HERMES_HOME"]
    candidates = []
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE")
    if home:
        candidates.append(os.path.join(home, "AppData", "Local", "hermes"))
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        candidates.append(os.path.join(xdg, "hermes"))
    candidates.append(os.path.join(os.getcwd(), "state.db"))
    for c in candidates:
        if os.path.isfile(c):  # treat as state.db path or dir containing it
            d = c if os.path.isdir(c) else os.path.dirname(c)
            if os.path.isfile(os.path.join(d, "state.db")):
                return d
    # fall back to first dir-shaped candidate that simply exists
    for c in candidates:
        if os.path.isdir(c):
            return c
    raise SystemExit(
        "Could not auto-detect HERMES_HOME (no state.db found). "
        "Set HERMES_HOME or pass --hermes-home."
    )


def iso(ts_real):
    return datetime.datetime.fromtimestamp(ts_real, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def channel_of(source):
    # Stage 2 decision 2: store sessions.source verbatim as the channel value.
    # Distinct values observed in state.db: whatsapp, cli, cron, webui, subagent.
    return source


def actor_of(source, role):
    if role == "user":
        return "user"
    if source == "cron":
        return "cron"
    return "hermes"


def kind_of(role, urls_json):
    if urls_json:
        return "link"
    if role == "user":
        return "note"
    if role == "tool":
        return "command"
    return "reply"


def sp(ts_utc):
    return datetime.datetime.strptime(ts_utc, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=datetime.timezone.utc
    ).astimezone(SP_TZ).strftime("%Y-%m-%d %H:%M:%S %z")


def main():
    ap = argparse.ArgumentParser(description="Backfill interactions.db from state.db")
    ap.add_argument("--hermes-home", default=None, help="Hermes data dir (else HERMES_HOME/env auto-detect)")
    ap.add_argument("--state-db", default=None, help="path to state.db (else HERMES_HOME/state.db)")
    ap.add_argument("--out", default=None, help="path to interactions.db (else HERMES_HOME/interactions.db)")
    ap.add_argument("--no-drop", action="store_true", help="do not drop existing interactions.db first")
    args = ap.parse_args()

    home = detect_hermes_home(args.hermes_home)
    STATE_DB = args.state_db or os.path.join(home, "state.db")
    INTER_DB = args.out or os.path.join(home, "interactions.db")

    if not os.path.isfile(STATE_DB):
        raise SystemExit(f"state.db not found at {STATE_DB}")

    print(f"HERMES_HOME = {home}")
    print(f"STATE_DB    = {STATE_DB}")
    print(f"INTER_DB    = {INTER_DB}")

    if os.path.exists(INTER_DB) and not args.no_drop:
        os.remove(INTER_DB)
        print("removed existing interactions.db for clean backfill")

    inter = sqlite3.connect(INTER_DB)
    inter.executescript(SCHEMA)
    inter.execute("PRAGMA journal_mode=WAL")

    state = sqlite3.connect(STATE_DB)
    q = """
      SELECT m.session_id, s.source, m.role, m.timestamp, m.content
      FROM messages m JOIN sessions s ON m.session_id = s.id
      WHERE m.role IN ('user','assistant','tool')
      ORDER BY m.timestamp ASC
    """
    rows = state.execute(q).fetchall()
    print(f"source rows from state.db: {len(rows)}")

    ins = "INSERT INTO interactions (ts,channel,direction,actor,kind,content,urls,session_id,meta) VALUES (?,?,?,?,?,?,?,?,?)"
    n = 0
    for sid, source, role, ts, content in rows:
        content = content or ""
        urls = URL_RE.findall(content)
        urls_json = json.dumps(urls) if urls else None
        channel = channel_of(source)
        actor = actor_of(source, role)
        direction = "inbound" if actor == "user" else "outbound"
        kind = kind_of(role, urls_json)
        meta = json.dumps({"raw_source": source, "raw_role": role})
        inter.execute(ins, (iso(ts), channel, direction, actor, kind, content, urls_json, sid, meta))
        n += 1
    inter.commit()

    total = inter.execute("SELECT COUNT(*) FROM interactions").fetchone()[0]
    print(f"\nTOTAL ROWS INSERTED: {total}")
    print("\nBREAKDOWN BY CHANNEL x ACTOR:")
    for r in inter.execute("SELECT channel, actor, COUNT(*) FROM interactions GROUP BY channel, actor ORDER BY channel, actor"):
        print(f"  {r[0]:9} | {r[1]:7} | {r[2]}")
    print("\nBREAKDOWN BY KIND:")
    for r in inter.execute("SELECT kind, COUNT(*) FROM interactions GROUP BY kind ORDER BY COUNT(*) DESC"):
        print(f"  {r[0]:8} | {r[1]}")

    lo = inter.execute("SELECT MIN(ts) FROM interactions").fetchone()[0]
    hi = inter.execute("SELECT MAX(ts) FROM interactions").fetchone()[0]
    print(f"\nDATE RANGE (UTC): {lo}  ->  {hi}")
    print(f"DATE RANGE (America/Sao_Paulo): {sp(lo)} -> {sp(hi)}")

    print("\nKINDALIVE CHECK (urls LIKE '%kindalive%'):")
    for r in inter.execute("SELECT ts, urls, actor, channel FROM interactions WHERE urls LIKE '%kindalive%' ORDER BY ts"):
        print(f"  ts={r[0]} actor={r[2]} channel={r[3]} urls={r[1][:80]}")

    inter.close()
    state.close()


if __name__ == "__main__":
    main()
