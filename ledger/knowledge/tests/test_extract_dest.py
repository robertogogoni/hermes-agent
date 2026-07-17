import os
import sys
import importlib.util

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


schema = _load("knowledge_schema", os.path.join(REPO, "ledger", "knowledge", "schema.py"))
proposals = _load("knowledge_proposals", os.path.join(REPO, "ledger", "knowledge", "proposals.py"))
extractor = _load("knowledge_extract", os.path.join(REPO, "ledger", "knowledge", "extract.py"))
destination = _load("knowledge_dest", os.path.join(REPO, "ledger", "knowledge", "destinations.py"))


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "hm"
    h.mkdir()
    (h / "interactions.db").write_text("")
    schema.ensure_schema(str(h))
    return str(h)


def _seed_ledger(home, rows):
    """Insert rows directly into interactions.db for extraction tests."""
    import sqlite3
    db = os.path.join(home, "interactions.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS interactions (id INTEGER PRIMARY KEY, ts TEXT, channel TEXT, "
        "direction TEXT, actor TEXT, kind TEXT, content TEXT, urls TEXT, session_id TEXT, meta TEXT)"
    )
    conn.executemany(
        "INSERT INTO interactions (ts,channel,direction,actor,kind,content,urls,session_id,meta) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def test_decision_extractor_finds_decisions(home):
    _seed_ledger(home, [
        ("2026-07-17T10:00:00Z", "cli", "outbound", "hermes", "reply",
         "Decision: we will keep the gateway on the supervised restart path.", None, "s1", None),
        ("2026-07-17T10:05:00Z", "cli", "outbound", "hermes", "reply",
         "Just a normal reply with no decision.", None, "s1", None),
    ])
    ex = extractor.DecisionExtractor()
    props = ex.extract(home)
    assert len(props) == 1
    assert props[0]["subject"] == "gateway"
    assert "restart" in props[0]["object"].lower()


def test_extractor_stores_proposals(home):
    _seed_ledger(home, [
        ("2026-07-17T10:00:00Z", "cli", "outbound", "hermes", "reply",
         "Decision: we will keep the gateway on the supervised restart path.", None, "s1", None),
    ])
    ex = extractor.DecisionExtractor()
    created = extractor.run_extractor(home, ex, dry_run=False)
    assert created == 1
    pending = proposals.list_proposals(home, status="pending")
    assert len(pending) == 1


def test_destination_exports_facts_to_markdown(home):
    # Promote a fact first via proposal.
    pid = proposals.create_proposal(
        hermes_home=home, proposal_type="decision", subject="gateway", predicate="restart_policy",
        object="supervised", evidence=[(1, "decided")], extractor="x")
    proposals.review_proposal(home, pid, decision="approve", reviewer="rober")

    dest = destination.MarkdownDestination()
    out_dir = os.path.join(home, "wiki")
    written = dest.export(home, out_dir)
    assert written >= 1
    files = os.listdir(out_dir)
    assert any(f.endswith(".md") for f in files)
    # Markdown file must cite the ledger evidence.
    content = open(os.path.join(out_dir, files[0]), encoding="utf-8").read()
    assert "interactions.db" in content or "evidence" in content.lower()


def test_pluggable_extractor_contract(home):
    # Any object with extract(home) -> list[dict] qualifies.
    class MyExt:
        name = "custom"

        def extract(self, hermes_home):
            return [{"subject": "a", "predicate": "b", "object": "c",
                     "proposal_type": "fact", "evidence": [(1, None)], "extractor": "custom"}]

    ex = MyExt()
    created = extractor.run_extractor(home, ex, dry_run=False)
    assert created == 1
