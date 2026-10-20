"""SQLite storage. Tables are tidy and documented so the agent can query them."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

TZ = "Europe/Madrid"

SCHEMA = {
    "spot_price_hourly": "Day-ahead spot market price, hourly mean (EUR/MWh)",
    "pvpc_hourly": "Regulated retail price PVPC, hourly (EUR/MWh)",
    "demand_daily": "Electricity demand, daily total (MWh)",
    "generation_daily": "Peninsular generation by technology, daily total (MWh); one row per technology and day",
}


def _local(ts: pd.Series) -> pd.Series:
    return ts.dt.tz_convert(TZ).dt.strftime("%Y-%m-%d %H:%M")


def save_prices(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    """Store spot (resampled to hourly mean) and PVPC prices."""
    n = 0
    spot = df[df["series"] == "Spot market price"].copy()
    if not spot.empty:
        spot["ts_utc"] = spot["ts_utc"].dt.floor("h")
        spot = spot.groupby("ts_utc", as_index=False)["value"].mean()
        n += _upsert(conn, "spot_price_hourly", pd.DataFrame({
            "ts_utc": spot["ts_utc"].dt.strftime("%Y-%m-%dT%H:%MZ"),
            "ts_local": _local(spot["ts_utc"]),
            "price_eur_mwh": spot["value"].round(2),
        }))
    pvpc = df[df["series"] == "PVPC"]
    if not pvpc.empty:
        n += _upsert(conn, "pvpc_hourly", pd.DataFrame({
            "ts_utc": pvpc["ts_utc"].dt.strftime("%Y-%m-%dT%H:%MZ"),
            "ts_local": _local(pvpc["ts_utc"]),
            "price_eur_mwh": pvpc["value"].round(2),
        }))
    return n


def save_demand(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    d = df[df["series"] == "Demand"]
    return _upsert(conn, "demand_daily", pd.DataFrame({
        "date": d["ts_utc"].dt.tz_convert(TZ).dt.strftime("%Y-%m-%d"),
        "demand_mwh": d["value"].round(1),
    }))


def save_generation(conn: sqlite3.Connection, df: pd.DataFrame) -> int:
    g = df[df["series"] != "Total generation"]
    return _upsert(conn, "generation_daily", pd.DataFrame({
        "date": g["ts_utc"].dt.tz_convert(TZ).dt.strftime("%Y-%m-%d"),
        "technology": g["series"],
        "generation_mwh": g["value"].round(1),
    }))


_KEYS = {
    "spot_price_hourly": ["ts_utc"],
    "pvpc_hourly": ["ts_utc"],
    "demand_daily": ["date"],
    "generation_daily": ["date", "technology"],
}


def init_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS spot_price_hourly (ts_utc TEXT PRIMARY KEY, ts_local TEXT, price_eur_mwh REAL);
        CREATE TABLE IF NOT EXISTS pvpc_hourly (ts_utc TEXT PRIMARY KEY, ts_local TEXT, price_eur_mwh REAL);
        CREATE TABLE IF NOT EXISTS demand_daily (date TEXT PRIMARY KEY, demand_mwh REAL);
        CREATE TABLE IF NOT EXISTS generation_daily (date TEXT, technology TEXT, generation_mwh REAL,
                                                     PRIMARY KEY (date, technology));
    """)
    return conn


def _upsert(conn: sqlite3.Connection, table: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    cols = list(df.columns)
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
           f"ON CONFLICT({', '.join(_KEYS[table])}) DO UPDATE SET "
           + ", ".join(f"{c}=excluded.{c}" for c in cols if c not in _KEYS[table]))
    conn.executemany(sql, df.itertuples(index=False, name=None))
    conn.commit()
    return len(df)


def connect_readonly(path: str | Path) -> sqlite3.Connection:
    """Read-only connection: the database file cannot be modified through it."""
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)


def describe(conn: sqlite3.Connection) -> list[dict]:
    """Tables, columns, row counts and time coverage, for the agent."""
    out = []
    for table, doc in SCHEMA.items():
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        if not cols:
            continue
        tcol = "ts_local" if "ts_local" in cols else "date"
        n, lo, hi = conn.execute(f"SELECT COUNT(*), MIN({tcol}), MAX({tcol}) FROM {table}").fetchone()
        out.append({"table": table, "description": doc, "columns": cols, "rows": n, "from": lo, "to": hi})
    return out


def load_series(conn: sqlite3.Connection, series: str, technology: str | None = None,
                start: str | None = None, end: str | None = None) -> pd.Series:
    """Load a stored series indexed by local time: spot_price (hourly), demand or generation (daily)."""
    if series == "spot_price":
        sql, col, params = "SELECT ts_local AS t, price_eur_mwh AS v FROM spot_price_hourly WHERE 1=1", "ts_local", []
    elif series == "demand":
        sql, col, params = "SELECT date AS t, demand_mwh AS v FROM demand_daily WHERE 1=1", "date", []
    elif series == "generation":
        if not technology:
            raise ValueError("generation needs a technology (see the generation_daily table)")
        sql, col, params = ("SELECT date AS t, generation_mwh AS v FROM generation_daily WHERE technology = ?",
                            "date", [technology])
    else:
        raise ValueError(f"Unknown series '{series}'. Options: spot_price, demand, generation")
    if start:
        sql += f" AND {col} >= ?"; params.append(start)
    if end:
        sql += f" AND {col} <= ?"; params.append(end + (" 23:59" if col == "ts_local" and len(end) == 10 else ""))
    df = pd.read_sql_query(sql + f" ORDER BY {col}", conn, params=params)
    return pd.Series(df["v"].values, index=pd.to_datetime(df["t"]), name=technology or series)
