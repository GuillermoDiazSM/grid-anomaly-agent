"""Command line interface: grid-anomaly-agent {ingest, detect, ask}."""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from . import redata, store
from .agent import Agent
from .llm import OpenAICompatibleClient
from .tools import Toolbox

DEFAULT_DB = "data/grid-anomaly-agent.db"


def load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE lines from a .env file without overriding variables already set."""
    p = Path(path)
    if not p.exists():
        return
    import os
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def cmd_ingest(a):
    Path(a.db).parent.mkdir(parents=True, exist_ok=True)
    conn = store.init_db(a.db)
    savers = {"prices": store.save_prices, "demand": store.save_demand, "generation": store.save_generation}
    for widget in a.only or savers:
        print(f"Downloading {widget} {a.start} -> {a.end} ...", flush=True)
        n = savers[widget](conn, redata.fetch(widget, a.start, a.end))
        print(f"  {n} rows stored")
    for t in store.describe(conn):
        print(f"  {t['table']:20s} {t['rows']:>7} rows  {t['from']} -> {t['to']}")


def cmd_detect(a):
    out = Toolbox(store.connect_readonly(a.db)).detect_anomalies(
        a.series, a.technology, a.start, a.end, a.min_severity, a.top)
    if "error" in out:
        sys.exit(out["error"])
    print(f"{out['series']}: {out['points_scored']} points scored, by severity {out['severity_counts']}\n")
    for p in out["listed"]:
        print(f"  {p['time']:16s} {p['severity']:6s} value={p['value']:<12} last week={p['same_period_last_week']:<12} "
              f"z={p['z']}  ({p['method']})")


def cmd_ask(a):
    client = OpenAICompatibleClient(base_url=a.base_url, model=a.model)
    agent = Agent(client, Toolbox(store.connect_readonly(a.db)), max_steps=a.max_steps)
    res = agent.ask(a.question)
    if a.trace:
        for t in res.tool_calls:
            print(f"[{t['tool']}] {t['arguments']}\n  -> {t['output'][:400]}\n")
    print(res.answer)


def main(argv=None):
    load_dotenv()
    p = argparse.ArgumentParser(prog="grid-anomaly-agent",
                                description="Ask questions about Spanish electricity data (REData).")
    p.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database (default {DEFAULT_DB})")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("ingest", help="download data from REData into SQLite")
    i.add_argument("--start", type=date.fromisoformat, default=date.today() - timedelta(days=730))
    i.add_argument("--end", type=date.fromisoformat, default=date.today() - timedelta(days=1))
    i.add_argument("--only", nargs="+", choices=["prices", "demand", "generation"])
    i.set_defaults(func=cmd_ingest)

    d = sub.add_parser("detect", help="find anomalies in a series against its own history")
    d.add_argument("--series", choices=["demand", "generation", "spot_price"], default="demand")
    d.add_argument("--technology", help="for generation, e.g. Wind")
    d.add_argument("--start"); d.add_argument("--end")
    d.add_argument("--min-severity", choices=["high", "medium", "low"], default="medium")
    d.add_argument("--top", type=int, default=15)
    d.set_defaults(func=cmd_detect)

    q = sub.add_parser("ask", help="ask the agent a question")
    q.add_argument("question")
    q.add_argument("--base-url", help="OpenAI-compatible endpoint (default: $LLM_BASE_URL)")
    q.add_argument("--model", help="model name (default: $LLM_MODEL)")
    q.add_argument("--max-steps", type=int, default=8)
    q.add_argument("--trace", action="store_true", help="print the tool calls")
    q.set_defaults(func=cmd_ask)

    a = p.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    sys.exit(main())
