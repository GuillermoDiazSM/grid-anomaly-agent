"""Client for Red Eléctrica's public REData API (https://www.ree.es/en/apidatos).

No API key is required. A request cannot cross a calendar month (hourly and
daily-by-technology data) or a calendar year (daily demand), so long ranges are
split into calendar chunks.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
import requests

BASE_URL = "https://apidatos.ree.es/en/datos"
TIMEOUT = 60


@dataclass(frozen=True)
class Widget:
    """One REData widget and how to request it."""

    path: str
    time_trunc: str  # "hour" | "day"
    chunk: str       # "month" | "year": a request may not cross this boundary
    extra: str = ""


WIDGETS = {
    "prices": Widget("mercados/precios-mercados-tiempo-real", "hour", "month"),
    "demand": Widget("demanda/evolucion", "day", "year"),
    "generation": Widget(
        "generacion/estructura-generacion", "day", "month",
        "&geo_trunc=electric_system&geo_limit=peninsular&geo_ids=8741",
    ),
}


def _chunks(start: date, end: date, unit: str):
    """Split [start, end] into pieces that stay inside one calendar month or year."""
    cur = start
    while cur <= end:
        if unit == "month":
            nxt = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        else:
            nxt = date(cur.year + 1, 1, 1)
        stop = min(nxt - timedelta(days=1), end)
        yield cur, stop
        cur = stop + timedelta(days=1)


def parse_response(payload: dict) -> pd.DataFrame:
    """Flatten a REData response into long format: series, ts_utc, value."""
    if "errors" in payload:
        raise RuntimeError(f"REData error: {payload['errors']}")
    rows = []
    for item in payload.get("included", []):
        series = item["attributes"]["title"]
        for v in item["attributes"].get("values", []):
            rows.append((series, v["datetime"], v["value"]))
    df = pd.DataFrame(rows, columns=["series", "datetime", "value"])
    if df.empty:
        return df.assign(ts_utc=pd.Series(dtype="datetime64[ns, UTC]"))[["series", "ts_utc", "value"]]
    df["ts_utc"] = pd.to_datetime(df["datetime"], utc=True)
    return df[["series", "ts_utc", "value"]]


def fetch(widget: str, start: date, end: date, session: requests.Session | None = None,
          pause: float = 0.5) -> pd.DataFrame:
    """Download a widget for [start, end] (inclusive), chunking as needed."""
    w = WIDGETS[widget]
    s = session or requests.Session()
    frames = []
    for a, b in _chunks(start, end, w.chunk):
        url = (f"{BASE_URL}/{w.path}?start_date={a:%Y-%m-%d}T00:00"
               f"&end_date={b:%Y-%m-%d}T23:59&time_trunc={w.time_trunc}{w.extra}")
        for attempt in range(3):  # the API sometimes answers "temporarily unavailable"
            r = s.get(url, timeout=TIMEOUT)
            if r.status_code != 400 or attempt == 2:
                break
            time.sleep(5 * (attempt + 1))
        r.raise_for_status()
        frames.append(parse_response(r.json()))
        time.sleep(pause)
    df = pd.concat(frames, ignore_index=True) if frames else parse_response({})
    return df.drop_duplicates(["series", "ts_utc"]).reset_index(drop=True)
