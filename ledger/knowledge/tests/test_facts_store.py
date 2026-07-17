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
facts = _load("knowledge_facts", os.path.join(REPO, "ledger", "knowledge", "facts_store.py"))

import sys

NOW = "2026-07-17T12:00:00Z"


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "hm"
    h.mkdir()
    (h / "interactions.db").write_text("")
    schema.ensure_schema(str(h))
    return str(h)


def test_insert_fact_requires_evidence(home):
    with pytest.raises(ValueError):
        facts.insert_fact(
            hermes_home=home,
            subject="gateway", predicate="status", object="down",
            fact_type="fact", valid_from=NOW,
            evidence=[],  # empty evidence must be rejected (provenance-first)
        )


def test_insert_fact_stores_with_evidence(home):
    fid = facts.insert_fact(
        hermes_home=home,
        subject="gateway", predicate="status", object="down",
        fact_type="incident", valid_from=NOW,
        evidence=[(8231, "gateway killed at 00:43")],
        confidence=0.9,
    )
    assert fid is not None
    conn = schema.get_connection(home)
    row = conn.execute("SELECT * FROM knowledge_facts WHERE id=?", (fid,)).fetchone()
    assert row["subject"] == "gateway"
    assert row["status"] == "active"
    assert row["confidence"] == 0.9
    ev = conn.execute(
        "SELECT interaction_id, excerpt FROM knowledge_fact_evidence WHERE fact_id=?", (fid,)
    ).fetchall()
    assert len(ev) == 1 and ev[0]["interaction_id"] == 8231


def test_supersede_marks_old_and_links_new(home):
    f1 = facts.insert_fact(
        hermes_home=home, subject="policy", predicate="defender", object="enabled",
        fact_type="decision", valid_from="2026-01-01T00:00:00Z",
        evidence=[(1, "decided to keep Defender")],
    )
    f2 = facts.insert_fact(
        hermes_home=home, subject="policy", predicate="defender", object="disabled",
        fact_type="decision", valid_from="2026-06-01T00:00:00Z",
        evidence=[(2, "decided to remove Defender")],
        supersedes_fact_id=f1,
    )
    conn = schema.get_connection(home)
    old = conn.execute("SELECT status, valid_to FROM knowledge_facts WHERE id=?", (f1,)).fetchone()
    assert old["status"] == "superseded"
    assert old["valid_to"] == "2026-06-01T00:00:00Z"
    new = conn.execute("SELECT supersedes_fact_id FROM knowledge_facts WHERE id=?", (f2,)).fetchone()
    assert new["supersedes_fact_id"] == f1


def test_search_facts_active_only(home):
    facts.insert_fact(hermes_home=home, subject="a", predicate="is", object="x",
                      fact_type="fact", valid_from=NOW, evidence=[(1, "e")])
    facts.insert_fact(hermes_home=home, subject="b", predicate="is", object="y",
                      fact_type="fact", valid_from=NOW, evidence=[(2, "e")],
                      supersedes_fact_id=None)
    # mark one superseded manually to test filtering
    conn = schema.get_connection(home)
    conn.execute("UPDATE knowledge_facts SET status='superseded' WHERE subject='b'")
    conn.commit()
    rows = facts.search_facts(hermes_home=home, fact_type="fact", active_only=True)
    subjects = {r["subject"] for r in rows}
    assert subjects == {"a"}


def test_search_facts_by_subject(home):
    facts.insert_fact(hermes_home=home, subject="gateway", predicate="status", object="up",
                      fact_type="fact", valid_from=NOW, evidence=[(3, "e")])
    rows = facts.search_facts(hermes_home=home, subject="gateway")
    assert len(rows) == 1 and rows[0]["object"] == "up"


def test_get_fact_includes_evidence(home):
    fid = facts.insert_fact(hermes_home=home, subject="x", predicate="p", object="o",
                             fact_type="fact", valid_from=NOW,
                             evidence=[(10, "excerpt ten")])
    f = facts.get_fact(home, fid)
    assert f["id"] == fid
    assert f["evidence"][0]["interaction_id"] == 10
