"""Mock-CRM lookups, and the same lookups offered to Claude as tools.

Authorization lives HERE, not in the prompt. Every lookup takes the sender address from the
trusted email envelope, which the model never sees as an argument it can change:
- get_customer has no parameters at all: it always means "the person who sent this email";
- get_order takes an order ID and refuses any order that isn't on the sender's account.
So a prompt-injected or confused model can ask for anything; it can only ever get the
sender's own records.
"""

from __future__ import annotations

import logging
import re

import psycopg

from common import db

log = logging.getLogger(__name__)

# Same message for "doesn't exist" and "belongs to someone else", so nobody can use the
# reply to find out which order IDs are real.
NOT_ON_ACCOUNT = "No order with that ID on the account this email was sent from."

TOOLS = [
    {
        "name": "get_customer",
        "description": (
            "Look up the account of the person who sent this email: name and tier. "
            "Call it before answering any question about their account or orders. "
            "It takes no input: it always returns the sender's own account."
        ),
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "required": [],
                         "additionalProperties": False},
    },
    {
        "name": "get_order",
        "description": (
            "Look up one order by its ID (format LS-123456): status, item, amount, order date "
            "and any refund. Call it whenever the email mentions an order. Orders that are "
            "not on the sender's account are refused."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string", "description": "e.g. LS-482913"}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
    },
]


def normalize_order_id(raw: str) -> str:
    """'ls482913', '#LS-482913' and '482913' all mean LS-482913."""
    digits = re.sub(r"\D", "", raw or "")
    return f"LS-{digits}"


def get_customer(sender: str) -> dict | None:
    """The CRM record whose email matches the envelope sender, or None for a stranger."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id, name, email, tier FROM customers WHERE lower(email) = lower(%s)",
            (sender,),
        ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "email": row[2], "tier": row[3]}


def get_order(sender: str, order_id: str) -> dict:
    """{"ok": True, "order": {...}} if the order is on the sender's account, else refused."""
    order_id = normalize_order_id(order_id)
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT o.id, o.status, o.amount_cents, o.item, o.ordered_on,
                   r.status, r.amount_cents, r.created_on
            FROM orders o
            JOIN customers c ON c.id = o.customer_id
            LEFT JOIN refunds r ON r.order_id = o.id
            WHERE o.id = %s AND lower(c.email) = lower(%s)
            ORDER BY r.id DESC NULLS LAST
            LIMIT 1
            """,
            (order_id, sender),
        ).fetchone()
    if row is None:
        return {"ok": False, "order_id": order_id, "error": NOT_ON_ACCOUNT}
    refund = None
    if row[5] is not None:
        refund = {"status": row[5], "amount_cents": row[6], "created_on": row[7].isoformat()}
    return {"ok": True, "order": {
        "id": row[0], "status": row[1], "amount_cents": row[2],
        "amount": f"${row[2] / 100:,.2f}", "item": row[3],
        "ordered_on": row[4].isoformat(), "refund": refund,
    }}


class ToolBox:
    """Runs the tools for ONE email: binds the trusted sender and records every call."""

    def __init__(self, sender: str) -> None:
        self.sender = sender
        self.calls: list[dict] = []

    def run(self, name: str, args: dict) -> dict:
        """Run one tool call. A database error becomes a recorded failure, never a guess."""
        try:
            if name == "get_customer":
                customer = get_customer(self.sender)
                result = ({"ok": True, "customer": customer} if customer
                          else {"ok": False, "error": "The sender has no customer account."})
            elif name == "get_order":
                result = get_order(self.sender, args["order_id"])
            else:
                result = {"ok": False, "error": f"Unknown tool {name!r}."}
        except psycopg.Error:
            log.exception("tool %s failed", name)
            result = {"ok": False, "error": "Lookup failed; a person will follow up.",
                      "tool_error": True}
        self.calls.append({"name": name, "input": args, "result": result})
        return result
