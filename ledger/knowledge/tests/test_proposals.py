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
facts = _load("knowledge_facts", os.path.join(REPO, "ledger", "knowledge", "facts_store.py"))

NOW = "2026-07-17T12:00:00Z"


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "hm"
    h.mkdir()
    (h / "interactions.db").write_text("")
    schema.ensure_schema(str(h))
    return str(h)


def test_create_proposal_starts_pending(home):
    pid = proposals.create_proposal(
        hermes_home=home,
        proposal_type="decision",
        subject="x", predicate="is", object="y",
        evidence=[(1, "why")], extractor="test", rationale="r",
    )
    conn = schema.get_connection(home)
    row = conn.execute("SELECT status, extractor FROM knowledge_proposals WHERE id=?", (pid,)).fetchone()
    assert row["status"] == "pending"
    assert row["extractor"] == "test"


def test_review_approve_promotes_to_fact(home):
    pid = proposals.create_proposal(
        hermes_home=home, proposal_type="fact", subject="g", predicate="p", object="o",
        evidence=[(5, "e")], extractor="x",
    )
    fid = proposals.review_proposal(home, pid, decision="approve", reviewer="rober")
    assert fid is not None
    # fact exists with evidence
    f = facts.get_fact(home, fid)
    assert f["subject"] == "g"
    assert f["proposal_id"] == pid
    # proposal marked approved
    conn = schema.get_connection(home)
    row = conn.execute("SELECT status, reviewer FROM knowledge_proposals WHERE id=?", (pid,)).fetchone()
    assert row["status"] == "approved"
    assert row["reviewer"] == "rober"


def test_review_reject_does_not_promote(home):
    pid = proposals.create_proposal(
        hermes_home=home, proposal_type="fact", subject="a", predicate="b", object="c",
        evidence=[(9, "e")], extractor="x",
    )
    fid = proposals.review_proposal(home, pid, decision="reject", reviewer="rober", note="noise")
    assert fid is None
    conn = schema.get_connection(home)
    row = conn.execute("SELECT status FROM knowledge_proposals WHERE id=?", (pid,)).fetchone()
    assert row["status"] == "rejected"
    assert conn.execute("SELECT COUNT(*) FROM knowledge_facts").fetchone()[0] == 0


def test_list_pending(home):
    proposals.create_proposal(hermes_home=home, proposal_type="fact", subject="s", predicate="p", object="o", evidence=[(1, "e")], extractor="x")
    proposals.create_proposal(hermes_home=home, proposal_type="fact", subject="s2", predicate="p", object="o", evidence=[(2, "e")], extractor="x")
    conn = schema.get_connection(home)
    conn.execute("UPDATE knowledge_proposals SET status='approved' WHERE subject='s2'")
    conn.commit()
    pending = proposals.list_proposals(home, status="pending")
    assert len(pending) == 1 and pending[0]["subject"] == "s"


def test_promote_links_evidence_to_fact(home):
    pid = proposals.create_proposal(
        hermes_home=home, proposal_type="decision", subject="d", predicate="q", object="z",
        valid_from=NOW, evidence=[(7, "excerpt seven")], extractor="x",
    )
    fid = proposals.review_proposal(home, pid, decision="approve", reviewer="auto:policy")
    conn = schema.get_connection(home)
    ev = conn.execute(
        "SELECT interaction_id, excerpt FROM knowledge_fact_evidence WHERE fact_id=?", (fid,)
    ).fetchall()
    assert ev[0]["interaction_id"] == 7 and ev[0]["excerpt"] == "excerpt seven"
