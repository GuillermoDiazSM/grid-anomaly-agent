import sqlite3

import pytest

from grid_anomaly_agent import sqlguard


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "t.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE demand_daily (date TEXT, demand_mwh REAL)")
    c.executemany("INSERT INTO demand_daily VALUES (?, ?)", [(f"2025-01-{d:02d}", 700000 + d) for d in range(1, 31)])
    c.commit()
    c.close()
    return sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)


def test_select_works(conn):
    out = sqlguard.run(conn, "SELECT COUNT(*) AS n FROM demand_daily")
    assert out["columns"] == ["n"] and out["rows"] == [[30]]


def test_cte_works(conn):
    out = sqlguard.run(conn, "WITH x AS (SELECT demand_mwh FROM demand_daily) SELECT MAX(demand_mwh) FROM x")
    assert out["rows"] == [[700030.0]]


def test_rows_are_capped(conn):
    out = sqlguard.run(conn, "SELECT * FROM demand_daily", max_rows=10)
    assert len(out["rows"]) == 10 and out["truncated"]


@pytest.mark.parametrize("sql", [
    "DELETE FROM demand_daily",
    "DROP TABLE demand_daily",
    "UPDATE demand_daily SET demand_mwh = 0",
    "SELECT 1; DROP TABLE demand_daily",
    "PRAGMA table_info(demand_daily)",
    "ATTACH DATABASE 'x.db' AS x",
    "",
])
def test_rejected(conn, sql):
    with pytest.raises(sqlguard.SQLRejected):
        sqlguard.run(conn, sql)


def test_comments_cannot_hide_a_second_statement(conn):
    with pytest.raises(sqlguard.SQLRejected):
        sqlguard.run(conn, "SELECT 1 /* harmless */; DELETE FROM demand_daily -- end")


def test_read_only_connection_blocks_writes_even_without_the_validator(conn):
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM demand_daily")
