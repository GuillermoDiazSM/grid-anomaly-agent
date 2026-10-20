"""Guardrails for SQL written by the model.

Defence in depth:
1. Static checks: one statement, SELECT/WITH only, no dangerous keywords.
2. The connection is opened read-only (store.connect_readonly).
3. A SQLite authorizer denies anything that is not a read.
4. Results are capped and queries are time-limited.
"""
from __future__ import annotations

import re
import sqlite3
import time

MAX_ROWS = 200
TIMEOUT_S = 5.0

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum|reindex|"
    r"analyze|begin|commit|rollback|savepoint|release|load_extension)\b", re.IGNORECASE)


class SQLRejected(ValueError):
    """The query was rejected by the guardrails."""


def _strip(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return sql.strip().rstrip(";").strip()


def validate(sql: str) -> str:
    """Return the cleaned query or raise SQLRejected with a reason the model can act on."""
    q = _strip(sql)
    if not q:
        raise SQLRejected("Empty query.")
    if ";" in q:
        raise SQLRejected("Only one statement is allowed.")
    if not re.match(r"^(select|with)\b", q, re.IGNORECASE):
        raise SQLRejected("Only SELECT queries (optionally starting with WITH) are allowed.")
    m = _FORBIDDEN.search(q)
    if m:
        raise SQLRejected(f"Keyword '{m.group(0)}' is not allowed: the database is read-only.")
    return q


_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                    getattr(sqlite3, "SQLITE_RECURSIVE", 33)}  # WITH RECURSIVE


def _authorizer(action, *_):
    return sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY


def run(conn: sqlite3.Connection, sql: str, max_rows: int = MAX_ROWS,
        timeout_s: float = TIMEOUT_S) -> dict:
    """Validate and execute a query. Returns columns, rows and whether it was truncated."""
    q = validate(sql)
    deadline = time.monotonic() + timeout_s
    conn.set_authorizer(_authorizer)
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    try:
        cur = conn.execute(q)
        rows = cur.fetchmany(max_rows + 1)
    except sqlite3.DatabaseError as e:
        raise SQLRejected(f"Query failed: {e}") from e
    finally:
        conn.set_authorizer(None)
        conn.set_progress_handler(None, 0)
    cols = [d[0] for d in cur.description]
    return {"columns": cols, "rows": [list(r) for r in rows[:max_rows]],
            "truncated": len(rows) > max_rows}
