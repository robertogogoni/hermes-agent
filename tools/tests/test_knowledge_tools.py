import os
import sys
import importlib.util

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


schema = _load("knowledge_schema", os.path.join(REPO, "ledger", "knowledge", "schema.py"))
proposals = _load("knowledge_proposals", os.path.join(REPO, "ledger", "knowledge", "proposals.py"))
facts = _load("knowledge_facts", os.path.join(REPO, "ledger", "knowledge", "facts_store.py"))
tool_mod = _load("knowledge_tools", os.path.join(REPO, "tools", "knowledge.py"))


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "hm"
    h.mkdir()
    (h / "interactions.db").write_text("")
    schema.ensure_schema(str(h))
    # Force HERMES_HOME so the tool resolves to our temp db.
    os.environ["HERMES_HOME"] = str(h)
    return str(h)


def test_knowledge_search_returns_facts(home):
    facts.insert_fact(hermes_home=home, subject="gateway", predicate="status", object="up",
                      fact_type="fact", valid_from="2026-07-17T00:00:00Z", evidence=[(3, "e")])
    out = tool_mod.knowledge_search(subject="gateway")
    res = _json(out)
    assert res["status"] == "ok"
    assert len(res["facts"]) == 1
    assert res["facts"][0]["subject"] == "gateway"


def test_knowledge_promote_approves_and_returns_fact_id(home):
    pid = proposals.create_proposal(hermes_home=home, proposal_type="decision", subject="x", predicate="is", object="y", evidence=[(1, "e")], extractor="x")
    out = tool_mod.knowledge_promote(proposal_id=pid, decision="approve", reviewer="rober")
    res = _json(out)
    assert res["status"] == "approved"
    assert res["fact_id"] is not None


def test_knowledge_promote_rejects(home):
    pid = proposals.create_proposal(hermes_home=home, proposal_type="decision", subject="x", predicate="is", object="y", evidence=[(1, "e")], extractor="x")
    out = tool_mod.knowledge_promote(proposal_id=pid, decision="reject", reviewer="rober")
    res = _json(out)
    assert res["status"] == "rejected"
    assert res["fact_id"] is None


def _json(tool_result_str):
    import json
    return json.loads(tool_result_str)
