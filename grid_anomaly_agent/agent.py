"""Tool-calling agent loop over the Spanish electricity data."""
from __future__ import annotations

from dataclasses import dataclass, field

from .llm import ChatClient
from .tools import Toolbox

SYSTEM_PROMPT = """You are a data analyst for the Spanish electricity system.
You answer questions using ONLY the data returned by your tools:
- list_tables: what data exists and for which dates.
- run_sql: read-only SQL over the local SQLite database.
- detect_anomalies: unusual values of demand, generation or spot price compared with their own history.
- read_document: the project documentation (method, severities, limitations, data dictionary).

Rules:
1. Every number in your answer must come from a tool result in this conversation. Never use memory.
2. If the data needed is not available (e.g. dates outside the coverage), say so plainly.
3. Call list_tables before writing SQL unless you already know the exact table and column names.
4. Compute every derived number (percentages, ratios, differences, changes) inside the SQL query.
   Never do arithmetic yourself: report the values exactly as the query returns them.
5. Prefer aggregating in SQL over fetching many rows.
6. Keep answers short: the result first, then one line on how you obtained it.
7. When reporting anomalies, give the date, the value, last week's value and the severity.
8. To explain how something was computed, what a severity means, or why a point may be a false
   alarm, read the documentation first and base the explanation on it.
9. Units: prices in EUR/MWh, demand and generation in MWh. Times are Europe/Madrid."""


@dataclass
class Result:
    answer: str
    steps: int
    tool_calls: list[dict] = field(default_factory=list)
    stopped: str = "answer"  # "answer" | "max_steps"


class Agent:
    def __init__(self, client: ChatClient, toolbox: Toolbox, max_steps: int = 8):
        self.client = client
        self.toolbox = toolbox
        self.max_steps = max_steps

    def ask(self, question: str) -> Result:
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": question}]
        trace = []
        for step in range(1, self.max_steps + 1):
            msg = self.client.chat(messages, tools=self.toolbox.specs())
            calls = msg.get("tool_calls") or []
            if not calls:
                return Result(answer=(msg.get("content") or "").strip(), steps=step, tool_calls=trace)
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for c in calls:
                name, args = c["function"]["name"], c["function"].get("arguments", "{}")
                output = self.toolbox.call(name, args)
                trace.append({"tool": name, "arguments": args, "output": output[:2000]})
                messages.append({"role": "tool", "tool_call_id": c.get("id", name), "content": output})
        return Result(answer="I could not finish the analysis within the step limit.",
                      steps=self.max_steps, tool_calls=trace, stopped="max_steps")
