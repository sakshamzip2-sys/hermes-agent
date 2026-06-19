"""Live OR-recall fallback in SessionDB.search_messages.

FTS5 ANDs query terms, so a natural-language query whose filler words aren't
in the stored text returns nothing. search_messages now retries once with
stopwords stripped + terms OR-joined — but only when the strict search found
nothing, so precision is preserved.
"""

import tempfile
from pathlib import Path

import pytest

from hermes_state import SessionDB


@pytest.fixture()
def db():
    with tempfile.TemporaryDirectory() as tmp:
        d = SessionDB(db_path=Path(tmp) / "t.db")
        if not getattr(d, "_fts_enabled", False):
            pytest.skip("FTS5 unavailable in this SQLite build")
        d.create_session("s", "api_server")
        d.append_message(
            "s", "user", content="My favorite fruit is mango, especially Alphonso."
        )
        d.append_message(
            "s", "user", content="I deploy production services on Hetzner Cloud VMs."
        )
        yield d


def test_nl_query_recovered_by_or_fallback(db):
    # Strict AND requires "what"/"is"/"my" which aren't stored → would miss.
    res = db.search_messages("what is my favorite fruit", role_filter=["user"], limit=5)
    assert len(res) >= 1


def test_nl_deploy_query_recovered(db):
    res = db.search_messages("where do I deploy", role_filter=["user"], limit=5)
    assert len(res) >= 1


def test_unrelated_query_still_empty(db):
    # Fallback must not turn an irrelevant query into a false positive.
    assert db.search_messages("quantum chromodynamics lattice", role_filter=["user"], limit=5) == []
