import os
import tempfile
import sys
import importlib.util

import pytest

# Load the module under test without requiring package __init__ (tests run
# against a temp HERMES_HOME, never the real one).
# test file lives at <repo>/ledger/knowledge/tests/test_schema.py -> repo is 3 levels up.
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


schema = _load("knowledge_schema", os.path.join(REPO, "ledger", "knowledge", "schema.py"))


@pytest.fixture
def home(tmp_path):
    # Create a fake HERMES_HOME with an interactions.db marker so auto-detect works.
    h = tmp_path / "hm"
    h.mkdir()
    (h / "interactions.db").write_text("")  # marker only; schema.py only checks existence
    return str(h)


def test_ensure_schema_creates_tables(home):
    v = schema.ensure_schema(home)
    assert v == schema.SCHEMA_VERSION
    conn = schema.get_connection(home)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "knowledge_facts" in tables
    assert "knowledge_proposals" in tables
    assert "knowledge_fact_evidence" in tables
    assert "knowledge_proposal_evidence" in tables
    assert "knowledge_meta" in tables


def test_ensure_schema_idempotent(home):
    assert schema.ensure_schema(home) == schema.SCHEMA_VERSION
    # Running again must not raise and must stay at the same version.
    assert schema.ensure_schema(home) == schema.SCHEMA_VERSION
    conn = schema.get_connection(home)
    assert schema.current_schema_version(conn) == schema.SCHEMA_VERSION


def test_db_path_sits_next_to_interactions(home):
    p = schema.db_path(home)
    assert os.path.normpath(p) == os.path.normpath(
        os.path.join(home, "knowledge.db"))


def test_reset_for_tests_clears_data(home):
    # Establish schema first (as ensure_schema would in production).
    schema.ensure_schema(home)
    conn = schema.get_connection(home)
    conn.execute(
        "INSERT INTO knowledge_facts (subject,predicate,object,fact_type,learned_at,created_at) "
        "VALUES ('x','is','y','fact','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')")
    conn.commit()
    schema.reset_for_tests(home)
    conn2 = schema.get_connection(home)
    assert conn2.execute("SELECT COUNT(*) FROM knowledge_facts").fetchone()[0] == 0


def test_auto_detect_uses_marker_db(home):
    # interactions.db marker present -> home detected.
    assert schema._detect_hermes_home(home) == home
