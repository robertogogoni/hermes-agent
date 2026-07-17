import sqlite3
import threading

import pytest

from ledger import write
from ledger.interactions_search import _to_utc_iso


@pytest.fixture
def ledger_home(tmp_path):
    db = tmp_path / "interactions.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """CREATE TABLE interactions (
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
        )"""
    )
    conn.commit()
    conn.close()
    write._CONN = None
    write._DB_PATH = None
    yield str(tmp_path)
    if write._CONN is not None:
        write._CONN.close()
    write._CONN = None
    write._DB_PATH = None


def test_shared_writer_connection_is_safe_across_gateway_threads(ledger_home):
    """Regression: cron ran on a different thread than the cached connection."""
    results = []

    def insert(content):
        results.append(
            write.record_interaction(
                "cron", "outbound", "cron", content,
                kind="digest", hermes_home=ledger_home,
            )
        )

    first = threading.Thread(target=insert, args=("first",))
    second = threading.Thread(target=insert, args=("second",))
    first.start(); first.join()
    second.start(); second.join()

    assert all(row_id is not None for row_id in results)
    conn = sqlite3.connect(f"{ledger_home}/interactions.db")
    assert conn.execute("SELECT COUNT(*) FROM interactions").fetchone()[0] == 2
    assert conn.execute("SELECT DISTINCT kind FROM interactions").fetchall() == [("digest",)]
    conn.close()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-07-17T02:00:00Z", "2026-07-17T02:00:00Z"),
        ("2026-07-17 02:00 UTC", "2026-07-17T02:00:00Z"),
        ("2026-07-17 02:00 GMT", "2026-07-17T02:00:00Z"),
        ("2026-07-17T00:39:00-03:00", "2026-07-17T03:39:00Z"),
    ],
)
def test_digest_timestamp_formats_are_normalized(raw, expected):
    assert _to_utc_iso(raw) == expected