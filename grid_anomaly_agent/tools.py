"""Tools exposed to the agent. Each tool returns JSON-serialisable data."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pandas as pd

from . import anomaly, sqlguard, store

SPECS = [
    {"type": "function", "function": {
        "name": "list_tables",
        "description": "List the available tables with their columns, row counts and time coverage. "
                       "Call this first if you are unsure which data exists.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "run_sql",
        "description": "Run ONE read-only SQLite SELECT query and get up to 200 rows. "
                       "Timestamps: ts_local is 'YYYY-MM-DD HH:MM' (Europe/Madrid); dates are 'YYYY-MM-DD'. "
                       "Aggregate in SQL instead of fetching raw rows.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "A single SELECT (or WITH ... SELECT) statement."}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "detect_anomalies",
        "description": "Find unusual values of a series compared with its own history (same weekday or hour of "
                       "the previous week), using a robust z-score. Severities: high, medium, low, not_evaluable.",
        "parameters": {"type": "object", "properties": {
            "series": {"type": "string", "enum": ["demand", "generation", "spot_price"]},
            "technology": {"type": "string", "description": "Required for generation, e.g. 'Wind', 'Nuclear'."},
            "start": {"type": "string", "description": "Inclusive start date YYYY-MM-DD (optional)."},
            "end": {"type": "string", "description": "Inclusive end date YYYY-MM-DD (optional)."},
            "min_severity": {"type": "string", "enum": ["high", "medium", "low"],
                             "description": "Lowest severity to list, default medium."},
            "top": {"type": "integer", "description": "Maximum number of points to list, default 15."}},
            "required": ["series"]}}},
]

def _doc_spec(names: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": "read_document",
        "description": "Read the project documentation (Markdown). Use it to explain how the anomaly detection "
                       "works, what severities mean, known limitations, and what each table and column contains. "
                       f"Available documents: {', '.join(names) or 'none'}.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "enum": names},
            "section": {"type": "string", "description": "Optional heading to return only that section, "
                                                         "e.g. 'Known limitations'."}},
            "required": ["name"]}}}


def default_docs_dir() -> Path:
    """./docs if it exists (running from the repository), else the docs folder next to the package."""
    cwd = Path("docs")
    return cwd if cwd.is_dir() else Path(__file__).resolve().parent.parent / "docs"


_RANK = {"high": 3, "medium": 2, "low": 1}
# History loaded before `start` so the baseline exists from the first requested day.
_WARMUP = {"demand": pd.Timedelta(weeks=60), "generation": pd.Timedelta(weeks=60),
           "spot_price": pd.Timedelta(weeks=10)}


class Toolbox:
    def __init__(self, conn: sqlite3.Connection, docs_dir: str | Path | None = None):
        self.conn = conn
        self.docs_dir = Path(docs_dir) if docs_dir else default_docs_dir()

    def documents(self) -> list[str]:
        return sorted(p.stem for p in self.docs_dir.glob("*.md")) if self.docs_dir.is_dir() else []

    def specs(self) -> list[dict]:
        """Tool schemas sent to the model; the document list is read from disk."""
        docs = self.documents()
        return SPECS + ([_doc_spec(docs)] if docs else [])

    def list_tables(self) -> list[dict]:
        return store.describe(self.conn)

    def run_sql(self, query: str) -> dict:
        return sqlguard.run(self.conn, query)

    def detect_anomalies(self, series: str, technology: str | None = None, start: str | None = None,
                         end: str | None = None, min_severity: str = "medium", top: int = 15) -> dict:
        warm = (pd.Timestamp(start) - _WARMUP[series]).strftime("%Y-%m-%d") if start else None
        s = store.load_series(self.conn, series, technology, warm, end)
        if s.empty:
            return {"error": "No data for that series and period."}
        scored = anomaly.score(s, anomaly.PRESETS[series])
        if start:
            scored = scored[scored.index >= pd.Timestamp(start)]
        counts = scored["severity"].value_counts().to_dict()
        flagged = scored[scored["severity"].map(_RANK).fillna(0) >= _RANK[min_severity]].copy()
        flagged["abs_z"] = flagged["z"].abs().fillna(0)
        flagged = flagged.sort_values(["abs_z"], ascending=False).head(top)
        fmt = "%Y-%m-%d %H:%M" if series == "spot_price" else "%Y-%m-%d"
        points = [{"time": t.strftime(fmt), "value": round(r.value, 2),
                   "same_period_last_week": None if pd.isna(r.reference) else round(r.reference, 2),
                   "z": None if pd.isna(r.z) else round(r.z, 2), "severity": r.severity, "method": r.method}
                  for t, r in flagged.iterrows()]
        return {"series": technology or series, "points_scored": len(scored), "severity_counts": counts,
                "listed": points}

    def read_document(self, name: str, section: str | None = None) -> dict:
        if name not in self.documents():
            return {"error": f"Unknown document '{name}'. Available: {self.documents()}"}
        text = (self.docs_dir / f"{name}.md").read_text(encoding="utf-8")
        if not section:
            return {"name": name, "content": text}
        # Return the section whose heading contains `section`, down to the next heading of the same or higher level.
        lines = text.splitlines()
        for i, line in enumerate(lines):
            m = re.match(r"^(#+)\s+(.*)", line)
            if m and section.lower() in m.group(2).lower():
                level, out = len(m.group(1)), [line]
                for nxt in lines[i + 1:]:
                    h = re.match(r"^(#+)\s", nxt)
                    if h and len(h.group(1)) <= level:
                        break
                    out.append(nxt)
                return {"name": name, "section": m.group(2), "content": "\n".join(out).strip()}
        headings = [l.lstrip("#").strip() for l in lines if l.startswith("#")]
        return {"error": f"Section '{section}' not found in {name}. Headings: {headings}"}

    def call(self, name: str, arguments: str | dict) -> str:
        """Dispatch a tool call; errors are returned as text so the model can correct itself."""
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
            fn = {"list_tables": self.list_tables, "run_sql": self.run_sql,
                  "detect_anomalies": self.detect_anomalies, "read_document": self.read_document}.get(name)
            if fn is None:
                return json.dumps({"error": f"Unknown tool '{name}'."})
            return json.dumps(fn(**args), default=str)
        except sqlguard.SQLRejected as e:
            return json.dumps({"error": str(e)})
        except (TypeError, ValueError, KeyError) as e:
            return json.dumps({"error": f"Invalid arguments for {name}: {e}"})
