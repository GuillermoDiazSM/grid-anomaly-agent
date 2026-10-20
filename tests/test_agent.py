import json
import sqlite3

import pytest

from grid_anomaly_agent.agent import Agent
from grid_anomaly_agent.tools import Toolbox


class FakeLLM:
    """Replays scripted assistant messages and records what it was sent."""

    def __init__(self, script):
        self.script = list(script)
        self.seen = []

    def chat(self, messages, tools=None):
        self.seen.append(messages)
        return self.script.pop(0)


def call(name, args, id_="c1"):
    return {"content": "", "tool_calls": [
        {"id": id_, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


@pytest.fixture
def toolbox(tmp_path):
    db = tmp_path / "t.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE demand_daily (date TEXT PRIMARY KEY, demand_mwh REAL)")
    c.executemany("INSERT INTO demand_daily VALUES (?, ?)", [("2025-01-01", 600.0), ("2025-01-02", 800.0)])
    c.commit()
    c.close()
    return Toolbox(sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True))


def test_tool_result_reaches_the_model_and_answer_is_returned(toolbox):
    llm = FakeLLM([call("run_sql", {"query": "SELECT AVG(demand_mwh) FROM demand_daily"}),
                   {"content": "Average demand was 700.0 MWh."}])
    res = Agent(llm, toolbox).ask("What was the average demand?")
    assert res.answer == "Average demand was 700.0 MWh."
    assert res.steps == 2
    tool_msg = llm.seen[1][-1]
    assert tool_msg["role"] == "tool" and json.loads(tool_msg["content"])["rows"] == [[700.0]]


def test_rejected_sql_is_returned_to_the_model_as_an_error(toolbox):
    llm = FakeLLM([call("run_sql", {"query": "DELETE FROM demand_daily"}),
                   {"content": "I cannot modify the data."}])
    res = Agent(llm, toolbox).ask("Delete everything")
    assert "error" in json.loads(res.tool_calls[0]["output"])
    assert res.answer == "I cannot modify the data."


def test_unknown_tool_and_bad_arguments_do_not_crash(toolbox):
    assert "error" in json.loads(toolbox.call("nope", "{}"))
    assert "error" in json.loads(toolbox.call("run_sql", '{"sql": "SELECT 1"}'))


def test_step_limit(toolbox):
    llm = FakeLLM([call("list_tables", {}, f"c{i}") for i in range(3)])
    res = Agent(llm, toolbox, max_steps=3).ask("Loop forever")
    assert res.stopped == "max_steps" and len(res.tool_calls) == 3


@pytest.fixture
def docs(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "method.md").write_text("# Method\n\nIntro.\n\n## Severity\n\nhigh when |z| > 5\n\n## Limitations\n\nRebounds.\n",
                                 encoding="utf-8")
    return d


def test_read_document_whole_and_section(toolbox, docs):
    toolbox.docs_dir = docs
    assert "Rebounds" in toolbox.read_document("method")["content"]
    sec = toolbox.read_document("method", "severity")
    assert sec["content"].startswith("## Severity") and "Rebounds" not in sec["content"]
    assert "error" in toolbox.read_document("method", "nonexistent")
    assert "error" in toolbox.read_document("missing")


def test_document_tool_is_offered_only_when_docs_exist(toolbox, docs, tmp_path):
    toolbox.docs_dir = tmp_path / "empty"
    assert "read_document" not in [s["function"]["name"] for s in toolbox.specs()]
    toolbox.docs_dir = docs
    spec = [s for s in toolbox.specs() if s["function"]["name"] == "read_document"][0]
    assert spec["function"]["parameters"]["properties"]["name"]["enum"] == ["method"]
