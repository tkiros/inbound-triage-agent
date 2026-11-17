"""The triage workflow as a LangGraph graph. One run per email; the email id is the thread_id.

    classify -> check -> retrieve -> draft -> gate --no reasons--> send
                                                  \\--reasons--> approval --approve--> send
                                                                          \\--reject--> reject

PostgresSaver stores the state after every node, so a run paused at `approval` can be resumed
later, from another process, by app/approvals.py. Only `send` has side effects.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TypedDict

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from psycopg.types.json import Jsonb

from app import classify, draft, policies, risk, tools
from common import db

log = logging.getLogger(__name__)


class TriageState(TypedDict, total=False):
    email: dict            # id, sender (trusted envelope), subject, body
    offline: bool          # True: keyword classifier + template drafts, no model calls
    classification: dict   # intent, second_intent, urgency, risk, order_id
    customer: dict | None  # the sender's CRM record, looked up by code
    orders: dict           # order id -> get_order() result, looked up by code
    action: dict | None    # the refund code will issue if the reply is sent
    policies: list[dict]   # retrieved handbook sections
    draft: dict            # reply, cited_sections
    tool_calls: list[dict] # every tool call the drafter made, with its result
    hold_reasons: list[str]
    decision: str          # approve | reject (only for held emails)
    decided_by: str
    outcome: str           # sent | rejected


def offline_mode() -> bool:
    """TRIAGE_OFFLINE=1/0 decides; otherwise offline when no API key is set.

    Using `ant auth login` instead of a key? Set TRIAGE_OFFLINE=0.
    """
    flag = os.environ.get("TRIAGE_OFFLINE")
    if flag in ("0", "1"):
        return flag == "1"
    return not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


# Nodes: each takes the state and returns only the keys it changes. --------------------------

def classify_step(state: TriageState) -> dict:
    email = state["email"]
    result = classify.classify_offline(email) if state["offline"] else classify.classify(email)
    return {"classification": result.model_dump()}


def check_step(state: TriageState) -> dict:
    """Facts for the gate, looked up by code with the trusted sender (not by the model)."""
    email, c = state["email"], state["classification"]
    ids = risk.find_order_ids(f"{email['subject']} {email['body']}")
    if c["order_id"]:
        ids.append(tools.normalize_order_id(c["order_id"]))
    orders = {oid: tools.get_order(email["sender"], oid) for oid in dict.fromkeys(ids)}
    return {"customer": tools.get_customer(email["sender"]), "orders": orders,
            "action": risk.propose_refund(c, orders)}


def retrieve_step(state: TriageState) -> dict:
    """Search the handbook with the email's words plus hints from its intent and the facts."""
    tags = [state["classification"]["intent"], *risk.text_flags(state["email"])]
    action = state["action"]
    if action and action["amount_cents"] > risk.REFUND_REVIEW_CENTS:
        tags.append("large_refund")
    if any(not r["ok"] for r in state["orders"].values()):
        tags.append("order_not_on_account")
    return {"policies": policies.search(policies.query_for(state["email"], tags), k=3)}


def draft_step(state: TriageState) -> dict:
    toolbox = tools.ToolBox(state["email"]["sender"])
    make = draft.draft_offline if state["offline"] else draft.draft
    result = make(state["email"], state["classification"], state["policies"], state["action"],
                  toolbox)
    return {"draft": result.model_dump(), "tool_calls": toolbox.calls}


def gate_step(state: TriageState) -> dict:
    return {"hold_reasons": risk.hold_reasons(state)}


# Everything before the human: also run directly by the eval harness (app/pipeline.py).
ANALYSIS_STEPS = [("classify", classify_step), ("check", check_step),
                  ("retrieve", retrieve_step), ("draft", draft_step), ("gate", gate_step)]


def approval_step(state: TriageState) -> dict:
    """Pause until a person decides. No side effects here: on resume, LangGraph runs this node
    again from its first line, and interrupt() then returns the person's answer."""
    answer = interrupt({
        "email_id": state["email"]["id"], "sender": state["email"]["sender"],
        "subject": state["email"]["subject"], "hold_reasons": state["hold_reasons"],
        "action": state["action"], "draft": state["draft"]["reply"],
    })
    return {"decision": answer["decision"], "decided_by": answer["by"]}


def send_step(state: TriageState) -> dict:
    """"Send" = write the outbox row (and issue the proposed refund) in one transaction.

    The outbox is keyed by email id, so if this node ever runs twice for an email, the second
    run inserts nothing and sends nothing.
    """
    email, action = state["email"], state["action"]
    subject = email["subject"] if email["subject"].lower().startswith("re:") \
        else f"Re: {email['subject']}"
    with db.connect() as conn, conn.transaction():
        new = conn.execute(
            "INSERT INTO outbox (email_id, to_address, subject, body) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (email_id) DO NOTHING RETURNING email_id",
            (email["id"], email["sender"], subject, state["draft"]["reply"]),
        ).fetchone()
        if new and action:
            conn.execute(
                "INSERT INTO refunds (order_id, amount_cents, status, email_id)"
                " VALUES (%s, %s, 'issued', %s) ON CONFLICT (email_id) DO NOTHING",
                (action["order_id"], action["amount_cents"], email["id"]),
            )
    if new:
        log.info("SENT reply to %s for email %s (refund: %s)", email["sender"], email["id"],
                 action["order_id"] if action else "none")
    else:
        log.info("email %s was already sent; nothing sent again", email["id"])
    return {"outcome": "sent"}


def reject_step(state: TriageState) -> dict:
    log.info("email %s rejected by %s; nothing sent", state["email"]["id"], state["decided_by"])
    return {"outcome": "rejected"}


def after_gate(state: TriageState) -> str:
    return "approval" if state["hold_reasons"] else "send"


def after_approval(state: TriageState) -> str:
    return "send" if state["decision"] == "approve" else "reject"


def build_graph(checkpointer):
    g = StateGraph(TriageState)
    previous = START
    for name, step in ANALYSIS_STEPS:
        g.add_node(name, step)
        g.add_edge(previous, name)
        previous = name
    g.add_node("approval", approval_step)
    g.add_node("send", send_step)
    g.add_node("reject", reject_step)
    g.add_conditional_edges("gate", after_gate, ["approval", "send"])
    g.add_conditional_edges("approval", after_approval, ["send", "reject"])
    g.add_edge("send", END)
    g.add_edge("reject", END)
    return g.compile(checkpointer=checkpointer)


@contextmanager
def open_graph() -> Iterator:
    """The graph with its Postgres checkpointer, for the worker and the approvers."""
    with PostgresSaver.from_conn_string(os.environ["DATABASE_URL"]) as saver:
        saver.setup()  # creates the checkpoint tables the first time; a no-op after that
        yield build_graph(saver)


def thread(email_id: str) -> dict:
    return {"configurable": {"thread_id": email_id}}


def save_outcome(email_id: str, result: dict) -> str:
    """Copy a run's result onto the emails row (what `make pending` and dashboards read)."""
    status = "awaiting_approval" if "__interrupt__" in result else result["outcome"]
    with db.connect() as conn:
        conn.execute(
            "UPDATE emails SET status = %s, intent = %s, hold_reasons = %s, draft = %s,"
            " proposed_action = %s WHERE id = %s",
            (status, result["classification"]["intent"], result["hold_reasons"],
             result["draft"]["reply"], Jsonb(result["action"]) if result["action"] else None,
             email_id),
        )
    return status
