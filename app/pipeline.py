"""The systems `make eval` compares. Each takes one email and returns a prediction dict.

predict()  -> the real pipeline: the graph's steps up to the risk gate, with the model
offline()  -> the same steps in offline mode (keyword classifier, template drafts, no model)
baseline() -> one prompt that classifies and drafts in a single call: no tools, rules or retrieval

The eval stops at the gate, so nothing is sent and no human wait is timed.
"""

from __future__ import annotations

from pydantic import Field

from app.classify import Classification, email_as_json
from app.graph import ANALYSIS_STEPS
from common import llm


def _run_steps(email: dict, offline: bool) -> dict:
    state = {"email": dict(email), "offline": offline}
    for _name, step in ANALYSIS_STEPS:
        state |= step(state)
    return {
        "intent": state["classification"]["intent"],
        "urgency": state["classification"]["urgency"],
        "hold": bool(state["hold_reasons"]),
        "hold_reasons": state["hold_reasons"],
        "tool_calls": state["tool_calls"],
        "policies": [p["id"] for p in state["policies"]],
        "action": state["action"],
    }


def predict(email: dict) -> dict:
    return _run_steps(email, offline=False)


def offline(email: dict) -> dict:
    return _run_steps(email, offline=True)


class BaselineReply(Classification):
    reply: str = Field(description="The reply email to send to the customer.")


BASELINE_SYSTEM = """You handle the customer-support inbox of Larkspur Supply Co., an online \
outdoor-gear shop. Classify the email and write a reply to it.
intent: order_status, refund, cancel_order, billing, account_update, product_question, \
phishing or other. urgency: low, normal or high. risk: high if a person should review the \
reply before it is sent, otherwise low. order_id: as written, or null."""


def baseline(email: dict) -> dict:
    out = llm.call(f"The email, as JSON:\n{email_as_json(email)}", step="baseline",
                   schema=BaselineReply, tier="small", system=BASELINE_SYSTEM, max_tokens=4096)
    return {
        "intent": out.intent,
        "urgency": out.urgency,
        "hold": out.risk == "high",
        "hold_reasons": ["model_risk_high"] if out.risk == "high" else [],
        "tool_calls": None,  # no tools, no retrieval: those metrics are n/a
        "policies": None,
        "action": None,
    }
