"""Test helpers: a fake Messages API (real SDK, mocked HTTP) and picks from the mock CRM."""

from __future__ import annotations

import json
import re

import anthropic
import httpx2

from app import classify
from common import llm
from data import crm_gen


def text(s: str) -> dict:
    return {"type": "text", "text": s}


def tool_use(block_id: str, name: str, args: dict) -> dict:
    return {"type": "tool_use", "id": block_id, "name": name, "input": args}


def install_fake_api(monkeypatch, respond) -> list[dict]:
    """Point common.llm at a fake API. respond(request_body) -> (content blocks, stop_reason).

    Returns the list of request bodies the fake received, in order.
    """
    seen: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        seen.append(body)
        content, stop = respond(body)
        return httpx2.Response(200, json={
            "id": f"msg_{len(seen)}", "type": "message", "role": "assistant",
            "model": body["model"], "content": content, "stop_reason": stop,
            "stop_sequence": None, "usage": {"input_tokens": 1000, "output_tokens": 100},
        })

    http = anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler))
    monkeypatch.setattr(llm, "_client",
                        anthropic.Anthropic(api_key="test", http_client=http, max_retries=0))
    return seen


def schema_fields(body: dict) -> set[str]:
    return set(body.get("output_config", {}).get("format", {}).get("schema", {})
               .get("properties", {}))


def is_tool_result_turn(body: dict) -> bool:
    last = body["messages"][-1]["content"]
    return isinstance(last, list) and last[0].get("type") == "tool_result"


def scripted_model(classification: dict, reply: str, cited: list[str],
                   order_id: str | None = None):
    """A fake model with fixed answers: classify -> `classification`; the draft loop first asks
    for get_customer (+ get_order(order_id)), then answers `reply` citing `cited`."""
    def respond(body):
        fields = schema_fields(body)
        if "intent" in fields and "reply" in fields:  # the baseline's one call
            return [text(json.dumps({**classification, "reply": reply}))], "end_turn"
        if "intent" in fields:
            return [text(json.dumps(classification))], "end_turn"
        if is_tool_result_turn(body):
            return [text(json.dumps({"reply": reply, "cited_sections": cited}))], "end_turn"
        calls = [tool_use("toolu_1", "get_customer", {})]
        if order_id:
            calls.append(tool_use("toolu_2", "get_order", {"order_id": order_id}))
        return calls, "tool_use"
    return respond


def email_in_prompt(body: dict) -> dict:
    """The email JSON the pipeline put in the prompt."""
    prompt = body["messages"][0]["content"]
    raw = prompt.split("The email, as JSON:\n", 1)[1]
    data, _ = json.JSONDecoder().raw_decode(raw)
    return {"id": "x", "sender": data["from"], "subject": data["subject"], "body": data["body"]}


def plausible_model(body: dict):
    """A fake model that gives sensible answers for any email, for end-to-end eval runs:
    it classifies with the keyword rules, looks up the order it finds, cites the first section."""
    fields = schema_fields(body)
    email = email_in_prompt(body)
    guess = classify.classify_offline(email).model_dump()
    if "intent" in fields and "reply" in fields:
        return [text(json.dumps({**guess, "reply": "Thanks, we're on it."}))], "end_turn"
    if "intent" in fields:
        return [text(json.dumps(guess))], "end_turn"
    if is_tool_result_turn(body):
        sections = re.findall(r"^(§\d+\.\d+) ", body["messages"][0]["content"], re.MULTILINE)
        return [text(json.dumps({"reply": "Thanks for writing. Larkspur Support",
                                 "cited_sections": sections[:1]}))], "end_turn"
    calls = [tool_use("toolu_1", "get_customer", {})]
    if guess["order_id"]:
        calls.append(tool_use("toolu_2", "get_order", {"order_id": guess["order_id"]}))
    return calls, "tool_use"


def crm_pick(statuses=("delivered",), min_cents=0, max_cents=10**9, tier="standard",
             skip: int = 0) -> tuple[str, str, int]:
    """(customer email, order id, amount in cents) of a real seeded order matching the filter."""
    customers, orders, _ = crm_gen.generate()
    by_id = {c[0]: c for c in customers}
    matches = [(by_id[o[1]][1], o[0], o[3]) for o in orders
               if o[2] in statuses and min_cents <= o[3] <= max_cents and by_id[o[1]][3] == tier]
    return matches[skip]


def someone_elses_order(sender: str) -> str:
    customers, orders, _ = crm_gen.generate()
    email_of = {c[0]: c[1] for c in customers}
    return next(o[0] for o in orders if email_of[o[1]] != sender)
