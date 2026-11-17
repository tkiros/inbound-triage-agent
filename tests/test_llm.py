"""common/llm.py against a fake Messages API (real SDK, mocked HTTP). No API key, no cost."""

import json
import os
from types import SimpleNamespace

import anthropic
import httpx2
import psycopg
import pytest
from pydantic import BaseModel, field_validator

from common import llm


class Out(BaseModel):
    label: str

    @field_validator("label")
    @classmethod
    def known(cls, v: str) -> str:
        if v not in {"refund", "other"}:
            raise ValueError("unknown label")
        return v


def reply(text: str, stop: str = "end_turn", **extra) -> dict:
    return {"text": text, "stop": stop, **extra}


def fake_api(monkeypatch, replies: list[dict], keep_db: bool = False) -> list[dict]:
    """Point llm at a fake API that answers with `replies` in order; return captured requests."""
    seen: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        seen.append({"headers": request.headers, "body": body})
        r = replies[len(seen) - 1]
        return httpx2.Response(200, json={
            "id": f"msg_{len(seen)}", "type": "message", "role": "assistant",
            "model": body["model"], "content": [{"type": "text", "text": r["text"]}],
            "stop_reason": r["stop"], "stop_sequence": None,
            "stop_details": r.get("stop_details"),
            "usage": {"input_tokens": 1000, "output_tokens": 100},
        })

    http = anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler))
    fake = anthropic.Anthropic(api_key="test", http_client=http, max_retries=0)
    monkeypatch.setattr(llm, "_client", fake)
    if not keep_db:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    return seen


def test_small_tier_structured_output(monkeypatch):
    seen = fake_api(monkeypatch, [reply('{"label": "refund"}')])
    out = llm.call("refund please", step="classify", schema=Out)
    assert out == Out(label="refund")
    body = seen[0]["body"]
    assert body["model"] == llm.MODELS["small"]
    assert body["output_config"]["effort"] == "low"
    assert "fallbacks" not in body  # Haiku has no server-side fallback


def test_schema_matches_what_messages_parse_sends(monkeypatch):
    seen = fake_api(monkeypatch, [reply('{"label": "refund"}'), reply('{"label": "refund"}')])
    llm.call("x", step="s", schema=Out)
    llm.client().messages.parse(
        model="m", max_tokens=10, messages=[{"role": "user", "content": "x"}], output_format=Out
    )
    assert seen[0]["body"]["output_config"]["format"] == seen[1]["body"]["output_config"]["format"]


def test_large_tier_turns_on_refusal_fallback(monkeypatch):
    seen = fake_api(monkeypatch, [reply("hello")])
    assert llm.call("hi", step="draft", tier="large") == "hello"
    req = seen[0]
    assert req["body"]["model"] == llm.MODELS["large"]
    assert req["body"]["fallbacks"] == "default"
    assert llm.FALLBACK_BETA in req["headers"]["anthropic-beta"]
    assert req["body"]["output_config"] == {"effort": "high"}


def test_refusal_raises(monkeypatch):
    details = {"type": "refusal", "category": "cyber", "explanation": "no"}
    fake_api(monkeypatch, [reply("", stop="refusal", stop_details=details)])
    with pytest.raises(llm.LLMError, match="refused"):
        llm.call("x", step="s", schema=Out)


def test_truncation_raises(monkeypatch):
    fake_api(monkeypatch, [reply('{"lab', stop="max_tokens")])
    with pytest.raises(llm.LLMError, match="max_tokens"):
        llm.call("x", step="s", schema=Out)


def test_invalid_reply_is_retried_with_the_error(monkeypatch):
    seen = fake_api(monkeypatch, [reply('{"label": "banana"}'), reply('{"label": "other"}')])
    assert llm.call("x", step="s", schema=Out) == Out(label="other")
    retry_messages = seen[1]["body"]["messages"]
    assert [m["role"] for m in retry_messages] == ["user", "assistant", "user"]
    assert "failed validation" in retry_messages[-1]["content"]


def test_gives_up_after_schema_retries(monkeypatch):
    seen = fake_api(monkeypatch, [reply('{"label": "banana"}')] * (llm.SCHEMA_RETRIES + 1))
    with pytest.raises(llm.LLMError, match="invalid after 3 attempts"):
        llm.call("x", step="s", schema=Out)
    assert len(seen) == llm.SCHEMA_RETRIES + 1


def usage(inp: int, out: int, **kw) -> SimpleNamespace:
    return SimpleNamespace(input_tokens=inp, output_tokens=out, **kw)


def test_cost_usd():
    assert llm.cost_usd("claude-haiku-5-5", usage(1000, 100)) == pytest.approx(0.00015)
    assert llm.cost_usd("claude-opus-5-5", usage(1000, 100)) == pytest.approx(0.006)
    assert llm.cost_usd("claude-haiku-5-5", usage(200_000, 1000)) == pytest.approx(0.1025)
    assert llm.cost_usd("some-new-model", usage(1, 1)) is None
    assert llm.cost_usd("claude-opus-5-5", usage(1, 1, cache_read_input_tokens=5)) is None
    fallback = [SimpleNamespace(type="message"), SimpleNamespace(type="fallback_message")]
    assert llm.cost_usd("claude-opus-5-5", usage(1, 1, iterations=fallback)) is None


def _db_up() -> bool:
    try:
        with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=2):
            return True
    except (KeyError, psycopg.Error):
        return False


@pytest.mark.skipif(not _db_up(), reason="needs `make up` (DATABASE_URL reachable)")
def test_every_attempt_is_recorded_in_llm_calls(monkeypatch):
    run_id = f"pytest-{os.getpid()}"
    monkeypatch.setenv("EVAL_RUN", run_id)
    monkeypatch.setenv("PROJECT_NAME", "pytest")
    fake_api(monkeypatch, [reply('{"label": "banana"}'), reply('{"label": "refund"}')],
             keep_db=True)
    llm.call("x", step="classify", schema=Out)
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        rows = conn.execute(
            "SELECT step, model, input_tokens, output_tokens, cost_usd FROM llm_calls"
            " WHERE run_id = %s ORDER BY id", (run_id,)).fetchall()
        conn.execute("DELETE FROM llm_calls WHERE run_id = %s", (run_id,))
    assert len(rows) == 2  # the failed attempt cost money too
    assert rows[0][:4] == ("classify", llm.MODELS["small"], 1000, 100)
    assert float(rows[0][4]) == pytest.approx(0.00015)


# call_with_tools: the tool-use loop ------------------------------------------------------------

TOOLS = [{"name": "lookup", "description": "d", "strict": True,
          "input_schema": {"type": "object", "properties": {"q": {"type": "string"}},
                           "required": ["q"], "additionalProperties": False}}]


def scripted(monkeypatch, turns, keep_db=False):
    """Fake API answering with (content blocks, stop_reason) turns in order; returns requests."""
    from tests.helpers import install_fake_api

    if not keep_db:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    calls: list[dict] = []

    def respond(body):
        calls.append(body)
        return turns[len(calls) - 1]
    install_fake_api(monkeypatch, respond)
    return calls


def use(block_id, q):
    return {"type": "tool_use", "id": block_id, "name": "lookup", "input": {"q": q}}


def test_tool_loop_returns_all_results_of_a_turn_in_one_message(monkeypatch):
    calls = scripted(monkeypatch, [
        ([use("t1", "a"), use("t2", "b")], "tool_use"),
        ([{"type": "text", "text": '{"label": "refund"}'}], "end_turn"),
    ])
    ran = []

    def run_tool(name, args):
        ran.append((name, args))
        return {"ok": args["q"] == "a", "value": args["q"]}

    out = llm.call_with_tools("x", step="draft", tools=TOOLS, run_tool=run_tool, schema=Out)
    assert out == Out(label="refund")
    assert ran == [("lookup", {"q": "a"}), ("lookup", {"q": "b"})]
    second = calls[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    results = second[2]["content"]  # ONE user message holding both results
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]
    assert [r["is_error"] for r in results] == [False, True]  # "ok": False -> error result
    assert json.loads(results[0]["content"]) == {"ok": True, "value": "a"}


def test_tool_loop_sends_strict_tools_with_auto_choice(monkeypatch):
    calls = scripted(monkeypatch, [([{"type": "text", "text": '{"label": "other"}'}], "end_turn")])
    llm.call_with_tools("x", step="draft", tools=TOOLS, run_tool=lambda n, a: {}, schema=Out)
    body = calls[0]
    assert body["tool_choice"] == {"type": "auto"}  # Opus/Sonnet 5.5 reject forced tool use
    assert body["tools"][0]["strict"] is True
    assert body["output_config"]["format"]["type"] == "json_schema"


def test_tool_loop_refusal_raises_and_tools_of_that_turn_never_run(monkeypatch):
    scripted(monkeypatch, [([use("t1", "a")], "refusal")])
    ran = []
    with pytest.raises(llm.LLMError, match="refused"):
        llm.call_with_tools("x", step="draft", tools=TOOLS, schema=Out,
                            run_tool=lambda n, a: ran.append(n) or {})
    assert ran == []


def test_tool_loop_truncation_raises(monkeypatch):
    scripted(monkeypatch, [([use("t1", "a")], "tool_use"),
                           ([{"type": "text", "text": '{"lab'}], "max_tokens")])
    with pytest.raises(llm.LLMError, match="max_tokens"):
        llm.call_with_tools("x", step="draft", tools=TOOLS, run_tool=lambda n, a: {"ok": True},
                            schema=Out)


def test_tool_loop_gives_up_after_max_rounds(monkeypatch):
    calls = scripted(monkeypatch, [([use(f"t{i}", "a")], "tool_use") for i in range(3)])
    with pytest.raises(llm.LLMError, match="still calling tools after 2 rounds"):
        llm.call_with_tools("x", step="draft", tools=TOOLS, run_tool=lambda n, a: {"ok": True},
                            schema=Out, max_tool_rounds=2)
    assert len(calls) == 3


def test_tool_loop_retries_an_invalid_final_answer(monkeypatch):
    calls = scripted(monkeypatch, [
        ([use("t1", "a")], "tool_use"),
        ([{"type": "text", "text": '{"label": "banana"}'}], "end_turn"),
        ([{"type": "text", "text": '{"label": "other"}'}], "end_turn"),
    ])
    out = llm.call_with_tools("x", step="draft", tools=TOOLS, run_tool=lambda n, a: {"ok": True},
                              schema=Out)
    assert out == Out(label="other")
    assert "failed validation" in calls[2]["messages"][-1]["content"]


@pytest.mark.skipif(not _db_up(), reason="needs `make up` (DATABASE_URL reachable)")
def test_tool_loop_records_every_attempt_in_llm_calls(monkeypatch):
    run_id = f"pytest-tools-{os.getpid()}"
    monkeypatch.setenv("EVAL_RUN", run_id)
    monkeypatch.setenv("PROJECT_NAME", "pytest")
    scripted(monkeypatch, [
        ([use("t1", "a")], "tool_use"),
        ([use("t2", "b")], "tool_use"),
        ([{"type": "text", "text": '{"label": "refund"}'}], "end_turn"),
    ], keep_db=True)
    llm.call_with_tools("x", step="draft", tools=TOOLS, run_tool=lambda n, a: {"ok": True},
                        schema=Out)
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        rows = conn.execute("SELECT step, stop_reason, cost_usd FROM llm_calls WHERE run_id = %s"
                            " ORDER BY id", (run_id,)).fetchall()
        conn.execute("DELETE FROM llm_calls WHERE run_id = %s", (run_id,))
    assert [(r[0], r[1]) for r in rows] == [("draft", "tool_use"), ("draft", "tool_use"),
                                           ("draft", "end_turn")]
    assert all(float(r[2]) == pytest.approx(0.00015) for r in rows)
