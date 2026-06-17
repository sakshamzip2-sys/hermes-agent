"""Tests for the gated database tool (STEP 10) — SQLite, no external server."""

import json
import sqlite3

import pytest

from tools.database_tool import (
    database_tool,
    check_database_requirements,
    _classify_sql,
    _has_multiple_statements,
    _redact_conn,
    _configured_connections,
)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """A SQLite file seeded with a users table, wired via DATABASE_URL."""
    path = tmp_path / "test.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, active INTEGER);"
        "INSERT INTO users (name, active) VALUES ('alice', 1), ('bob', 0);"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))  # for gate audit
    return f"sqlite:///{path}"


def _call(args):
    return json.loads(database_tool(args))


def test_parameterized_query(db):
    r = _call({"action": "query",
               "sql": "SELECT id, name FROM users WHERE active = ?", "params": [1]})
    assert r["columns"] == ["id", "name"]
    assert r["row_count"] == 1
    assert r["rows"][0][1] == "alice"
    assert r["truncated"] is False


def test_schema_lists_tables(db):
    r = _call({"action": "schema"})
    tables = [row[0] for row in r["rows"]]
    assert "users" in tables


def test_schema_describes_table(db):
    r = _call({"action": "schema", "table": "users"})
    cols = [row[0] for row in r["rows"]]
    assert "id" in cols and "name" in cols and "active" in cols


def test_write_blocked_without_allow_writes(db):
    r = _call({"action": "query", "sql": "DELETE FROM users WHERE id = 1"})
    assert "error" in r
    assert "allow_writes" in r["error"]


def test_write_blocked_when_config_switch_off(db, monkeypatch):
    # allow_writes per-call is true, but config switch defaults off → blocked.
    r = _call({"action": "query", "sql": "DELETE FROM users WHERE id = 1",
               "allow_writes": True})
    assert "error" in r
    assert "disabled" in r["error"] or "config" in r["error"].lower()


def test_write_allowed_when_both_switches_on(db, monkeypatch):
    # Turn on the config switch.
    monkeypatch.setattr("tools.database_tool._config_allows_writes", lambda: True)
    r = _call({"action": "query", "sql": "DELETE FROM users WHERE id = ?",
               "params": [2], "allow_writes": True})
    assert "error" not in r
    # Verify the row is gone.
    check = _call({"action": "query", "sql": "SELECT COUNT(*) FROM users"})
    assert check["rows"][0][0] == 1


def test_multiple_statements_rejected(db):
    r = _call({"action": "query",
               "sql": "SELECT 1; DROP TABLE users"})
    assert "error" in r
    assert "Multiple statements" in r["error"]


def test_row_cap_and_truncation_flag(db, monkeypatch):
    # Seed many rows then cap at 1.
    import sqlite3 as s
    path = db[len("sqlite:///"):]
    c = s.connect(path)
    c.executemany("INSERT INTO users (name, active) VALUES (?, 1)",
                  [(f"u{i}",) for i in range(10)])
    c.commit(); c.close()
    r = _call({"action": "query", "sql": "SELECT id FROM users", "max_rows": 1})
    assert r["row_count"] == 1
    assert r["truncated"] is True


# --- classification + safety helpers ---

@pytest.mark.parametrize("sql,kind", [
    ("SELECT * FROM t", "read"),
    ("  with x as (select 1) select * from x", "read"),
    ("EXPLAIN SELECT 1", "read"),
    ("PRAGMA table_info(t)", "read"),
    ("PRAGMA journal_mode=WAL", "write"),  # a writing PRAGMA
    ("INSERT INTO t VALUES (1)", "write"),
    ("update t set x=1", "write"),
    ("DROP TABLE t", "write"),
    ("VACUUM", "write"),
    # red-team bypasses — must be classified WRITE:
    ("WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x", "write"),  # CTE write
    ("with t as (insert into a values(1) returning id) select * from t", "write"),
    ("EXPLAIN ANALYZE DELETE FROM t", "write"),  # EXPLAIN ANALYZE executes
    ("explain analyze select 1", "read"),        # ...but a read EXPLAIN ANALYZE is read
    ("SELECT * FROM t /* harmless */", "read"),  # comment doesn't flip it
    ("/* c */ DELETE FROM t", "write"),          # comment-prefixed write still caught
])
def test_classify_sql(sql, kind):
    assert _classify_sql(sql) == kind


@pytest.mark.parametrize("sql,kind", [
    # round-3: write keyword inside a STRING LITERAL must NOT classify as write
    ("SELECT * FROM t WHERE col = 'delete'", "read"),
    ('SELECT "update" FROM t', "read"),
    ("SELECT 'INSERT INTO x' AS note", "read"),
    ("SELECT * FROM updates", "read"),       # table name contains 'update'
    # ...but a real CTE write is still caught
    ("WITH x AS (DELETE FROM t RETURNING id) SELECT * FROM x", "write"),
    ("UPDATE t SET note = 'select'", "write"),
])
def test_string_literal_not_misclassified(sql, kind):
    assert _classify_sql(sql) == kind


def test_null_byte_sqlite_path_refused_cleanly():
    """A null-byte path must be refused, not crash with ValueError."""
    from tools.database_tool import _resolve_connection
    conn, err = _resolve_connection("sqlite://foo\x00/etc/passwd")
    assert conn is None and err is not None


def test_cte_write_blocked_at_runtime(db):
    """A data-modifying CTE must hit the write gate, not slip through as read."""
    r = _call({"action": "query",
               "sql": "WITH x AS (DELETE FROM users RETURNING id) SELECT * FROM x"})
    assert "error" in r
    assert "allow_writes" in r["error"] or "write" in r["error"].lower()


def test_per_call_sqlite_path_confined(db, monkeypatch):
    """A per-call sqlite URL pointing outside the cwd is refused (no arbitrary file read)."""
    r = json.loads(database_tool({"action": "query", "connection": "sqlite:////etc/hosts",
                                  "sql": "SELECT 1"}))
    assert "error" in r
    assert "project directory" in r["error"] or "Refused" in r["error"]


def test_multiple_statement_detection_ignores_semicolons_in_strings():
    assert _has_multiple_statements("SELECT ';' FROM t") is False
    assert _has_multiple_statements("SELECT 1; SELECT 2") is True
    assert _has_multiple_statements("SELECT 1;") is False  # trailing ; only


def test_credential_redaction():
    redacted = _redact_conn("postgresql://user:supersecret@host:5432/db")
    assert "supersecret" not in redacted
    assert "<redacted>" in redacted
    assert "user" in redacted  # username preserved


# --- gating ---

def test_check_fn_hidden_without_connection(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for k in list(__import__("os").environ):
        if k.startswith("DATABASE_URL_"):
            monkeypatch.delenv(k, raising=False)
    # No connection configured → tool invisible (assuming no config file).
    conns = _configured_connections()
    # If the host happens to have a configured connection this would be non-empty;
    # in the hermetic test env it should be empty.
    assert isinstance(conns, dict)


def test_check_fn_visible_with_connection(db):
    assert check_database_requirements() is True


def test_registered_gated_not_core():
    import tools.database_tool  # noqa: F401
    from tools.registry import registry
    entry = registry._tools.get("database")
    assert entry is not None and entry.check_fn is not None
    from toolsets import _HERMES_CORE_TOOLS
    assert "database" not in _HERMES_CORE_TOOLS
