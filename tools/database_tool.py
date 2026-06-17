"""``database`` — parameterized SQL queries over a configured database (GATED).

Read-only by default. Materializes only when a connection string is configured
(``DATABASE_URL`` env or ``database.connections`` in config.yaml), so it adds no
schema cost to sessions that don't use a database.

Dialects (from the connection-string scheme):
  - ``sqlite://`` / a bare path  → stdlib ``sqlite3``
  - ``postgresql://`` / ``postgres://`` → ``psycopg`` (v3) if installed
  - ``mysql://``                 → ``pymysql`` if installed

Safety:
  - **Read-only by default.** Writes (INSERT/UPDATE/DELETE/DDL) are refused
    unless BOTH ``database.allow_writes: true`` (config — the non-overridable
    switch) AND ``allow_writes: true`` (per call) are set. Every write decision
    is recorded to the gate audit log.
  - Always parameterized: pass ``params`` separately; never interpolate.
  - Single statement only — stacked queries (``;``-separated) are rejected.
  - Connection-string credentials are redacted from all logs.
  - Rows are capped (``max_rows``) and the result flags truncation.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ROWS = 1000
_READ_ONLY_PREFIXES = ("select", "with", "explain", "pragma", "show", "describe", "desc")
_WRITE_PREFIXES = ("insert", "update", "delete", "replace", "merge", "create",
                   "drop", "alter", "truncate", "grant", "revoke", "attach", "vacuum")


def _redact_conn(conn: str) -> str:
    """Mask the password in a connection string for logging."""
    return re.sub(r"(://[^:/@]+:)([^@]+)(@)", r"\1<redacted>\3", conn or "")


def _configured_connections() -> Dict[str, str]:
    """Collect named connection strings from env + config."""
    conns: Dict[str, str] = {}
    env_url = os.getenv("DATABASE_URL")
    if env_url:
        conns["default"] = env_url
    for k, v in os.environ.items():
        if k.startswith("DATABASE_URL_") and v:
            conns[k[len("DATABASE_URL_"):].lower()] = v
    try:
        from hermes_cli.config import read_raw_config
        cfg = read_raw_config()
        db = cfg.get("database", {})
        if isinstance(db, dict):
            named = db.get("connections", {})
            if isinstance(named, dict):
                for name, url in named.items():
                    if isinstance(url, str) and url:
                        conns[str(name)] = url
    except Exception:
        pass
    return conns


def _config_allows_writes() -> bool:
    try:
        from hermes_cli.config import read_raw_config
        db = read_raw_config().get("database", {})
        return isinstance(db, dict) and bool(db.get("allow_writes", False))
    except Exception:
        return False


def _resolve_connection(connection: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Return (conn_string, error). ``connection`` may be a URL or a named ref."""
    conns = _configured_connections()
    if connection and "://" in connection:
        return connection, None  # explicit URL
    if not connection:
        if "default" in conns:
            return conns["default"], None
        if len(conns) == 1:
            return next(iter(conns.values())), None
        return None, ("No connection specified and no default configured. Set "
                      "DATABASE_URL or database.connections in config.yaml.")
    if connection in conns:
        return conns[connection], None
    return None, f"Unknown connection {connection!r}. Configured: {sorted(conns)}"


def _classify_sql(sql: str) -> str:
    """Return 'read' | 'write' | 'unknown' for the leading statement keyword."""
    stripped = sql.strip().lstrip("(").lower()
    first = stripped.split(None, 1)[0] if stripped else ""
    if first in _READ_ONLY_PREFIXES:
        # PRAGMA can write (e.g. PRAGMA journal_mode=...); only allow read forms.
        if first == "pragma" and "=" in sql:
            return "write"
        return "read"
    if first in _WRITE_PREFIXES:
        return "write"
    return "unknown"


def _has_multiple_statements(sql: str) -> bool:
    """Detect stacked statements (a ';' outside string literals with trailing SQL)."""
    in_s = in_d = False
    for i, ch in enumerate(sql):
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == ";" and not in_s and not in_d:
            if sql[i + 1:].strip():
                return True
    return False


def _run_sqlite(conn_str: str, sql: str, params, max_rows: int):
    import sqlite3
    # sqlite://path or sqlite:///abs/path or a bare path
    path = conn_str
    if conn_str.startswith("sqlite://"):
        path = conn_str[len("sqlite://"):]
        path = path.lstrip("/") if not path.startswith("/") else path
        if conn_str.startswith("sqlite:///"):
            path = "/" + conn_str[len("sqlite:///"):]
    conn = sqlite3.connect(path or ":memory:")
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(sql, tuple(params) if params else ())
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        out = [list(r) for r in rows[:max_rows]]
        conn.commit()
        return cols, out, truncated, None
    finally:
        conn.close()


def _run_postgres(conn_str: str, sql: str, params, max_rows: int):
    try:
        import psycopg
    except ImportError:
        return None, None, False, "psycopg (v3) is not installed for Postgres."
    with psycopg.connect(conn_str) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params) if params else None)
            cols = [d.name for d in cur.description] if cur.description else []
            rows = cur.fetchmany(max_rows + 1) if cur.description else []
            truncated = len(rows) > max_rows
            out = [list(r) for r in rows[:max_rows]]
        return cols, out, truncated, None


def _run_mysql(conn_str: str, sql: str, params, max_rows: int):
    try:
        import pymysql
    except ImportError:
        return None, None, False, "pymysql is not installed for MySQL."
    parts = urlsplit(conn_str)
    conn = pymysql.connect(
        host=parts.hostname or "localhost", port=parts.port or 3306,
        user=parts.username or "", password=parts.password or "",
        database=(parts.path or "/").lstrip("/"),
    )
    try:
        cur = conn.cursor()
        cur.execute(sql, tuple(params) if params else None)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(max_rows + 1) if cur.description else []
        truncated = len(rows) > max_rows
        out = [list(r) for r in rows[:max_rows]]
        conn.commit()
        return cols, out, truncated, None
    finally:
        conn.close()


def _dispatch(conn_str: str, sql: str, params, max_rows: int):
    scheme = urlsplit(conn_str).scheme.lower() if "://" in conn_str else "sqlite"
    if scheme in ("postgresql", "postgres"):
        return _run_postgres(conn_str, sql, params, max_rows)
    if scheme == "mysql":
        return _run_mysql(conn_str, sql, params, max_rows)
    return _run_sqlite(conn_str, sql, params, max_rows)


def _gate_write(sql: str, conn_str: str) -> Optional[str]:
    """Return an error string if the write is not permitted; None if allowed."""
    if not _config_allows_writes():
        return ("Write blocked: database writes are disabled. Set "
                "database.allow_writes: true in config.yaml to permit them "
                "(and pass allow_writes: true on the call).")
    return None


def database_tool(args: dict, **_kw) -> str:
    action = str(args.get("action", "query")).strip().lower()
    connection = args.get("connection")
    conn_str, err = _resolve_connection(connection)
    if err:
        return tool_error(err)

    if action == "schema":
        return _do_schema(conn_str, args)
    if action != "query":
        return tool_error(f"Unknown action {action!r}. Use query | schema.")

    sql = args.get("sql")
    if not sql or not isinstance(sql, str):
        return tool_error("'sql' is required.")
    if _has_multiple_statements(sql):
        return tool_error("Multiple statements are not allowed — run one at a time.")

    params = args.get("params") or []
    max_rows = int(args.get("max_rows", _DEFAULT_MAX_ROWS))
    kind = _classify_sql(sql)

    if kind == "write" or args.get("allow_writes"):
        # Any write requires explicit per-call opt-in AND the config switch.
        if kind == "write" and not args.get("allow_writes"):
            _audit("database", "blocked", sql, conn_str, "write without allow_writes")
            return tool_error("This statement writes data. Pass allow_writes: true "
                              "AND set database.allow_writes: true in config.yaml.")
        if kind == "write":
            gate_err = _gate_write(sql, conn_str)
            if gate_err:
                _audit("database", "blocked", sql, conn_str, "config disallows writes")
                return tool_error(gate_err)
            _audit("database", "allowed", sql, conn_str, "write permitted")

    if kind == "unknown":
        return tool_error("Could not classify the statement as read or write; refusing.")

    logger.info("database %s on %s", kind, _redact_conn(conn_str))
    try:
        cols, rows, truncated, run_err = _dispatch(conn_str, sql, params, max_rows)
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"Query failed: {exc}", kind="db_error")
    if run_err:
        return tool_error(run_err)
    return tool_result({
        "columns": cols, "rows": rows,
        "row_count": len(rows or []), "truncated": truncated,
    })


def _do_schema(conn_str: str, args: dict) -> str:
    table = args.get("table")
    scheme = urlsplit(conn_str).scheme.lower() if "://" in conn_str else "sqlite"
    if scheme in ("sqlite",) or "://" not in conn_str:
        if table:
            sql, p = "SELECT name, type FROM pragma_table_info(?)", [table]
        else:
            sql, p = ("SELECT name FROM sqlite_master WHERE type='table' "
                      "ORDER BY name", [])
    elif scheme in ("postgresql", "postgres"):
        if table:
            sql = ("SELECT column_name, data_type FROM information_schema.columns "
                   "WHERE table_name = %s ORDER BY ordinal_position")
            p = [table]
        else:
            sql = ("SELECT table_name FROM information_schema.tables "
                   "WHERE table_schema = 'public' ORDER BY table_name")
            p = []
    else:  # mysql
        if table:
            sql, p = ("SELECT column_name, data_type FROM information_schema.columns "
                      "WHERE table_name = %s", [table])
        else:
            sql, p = ("SELECT table_name FROM information_schema.tables "
                      "WHERE table_schema = DATABASE()", [])
    try:
        cols, rows, truncated, run_err = _dispatch(conn_str, sql, p, 5000)
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"Schema query failed: {exc}")
    if run_err:
        return tool_error(run_err)
    return tool_result({"columns": cols, "rows": rows, "table": table})


def _audit(action: str, verdict: str, sql: str, conn_str: str, reason: str) -> None:
    try:
        from tools.gate_audit import record_decision
        record_decision(action=action, verdict=verdict, reason=reason,
                        command=f"[{_redact_conn(conn_str)}] {sql}", env_type="database")
    except Exception:
        pass


def check_database_requirements() -> bool:
    """Invisible unless at least one connection string is configured."""
    try:
        return bool(_configured_connections())
    except Exception:
        return False


DATABASE_SCHEMA = {
    "name": "database",
    "description": (
        "Run a parameterized SQL query against a configured database (SQLite / "
        "Postgres / MySQL). READ-ONLY by default. action=query runs one "
        "statement (pass params separately — never interpolate values into "
        "sql); action=schema lists tables or one table's columns. Writes "
        "(INSERT/UPDATE/DELETE/DDL) require allow_writes:true here AND "
        "database.allow_writes:true in config. Connection comes from "
        "DATABASE_URL / config; pass 'connection' to pick a named one."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["query", "schema"],
                       "description": "query | schema. Default query."},
            "connection": {"type": "string",
                           "description": "A connection URL or a configured name. Omit for default."},
            "sql": {"type": "string", "description": "(query) one SQL statement; use ? / %s placeholders."},
            "params": {"type": "array", "description": "(query) values bound to the placeholders."},
            "table": {"type": "string", "description": "(schema) a table to describe; omit to list tables."},
            "allow_writes": {"type": "boolean",
                             "description": "Permit a write statement (also needs config switch). Default false."},
            "max_rows": {"type": "integer", "description": "Row cap. Default 1000 (truncate+flag)."},
        },
        "required": [],
    },
    "input_examples": [
        {"action": "query", "sql": "SELECT id, name FROM users WHERE active = ?", "params": [True]},
        {"action": "schema", "table": "users"},
    ],
}


registry.register(
    name="database",
    toolset="database",
    schema=DATABASE_SCHEMA,
    handler=database_tool,
    check_fn=check_database_requirements,
    emoji="🗄️",
    max_result_size_chars=200_000,
)
