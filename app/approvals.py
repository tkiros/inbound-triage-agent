"""Human approval: list held emails and resume their paused graph runs.

Used by the Slack buttons (app/slack_approver.py) and by the CLI:
    python -m app.approvals pending
    python -m app.approvals decide e012 approve      # or reject

Clicking twice is safe twice over: the first decision is stored in `approvals` and wins, and
the outbox row is keyed by email id, so even two resumes racing each other send one reply.
"""

from __future__ import annotations

import argparse
import getpass
import json

from langgraph.types import Command

from app.graph import open_graph, save_outcome, thread
from common import db

DECISIONS = ("approve", "reject")
PAST = {"approve": "approved", "reject": "rejected"}


def decide(email_id: str, decision: str, by: str) -> str:
    """Record a decision for a held email and resume its graph run. Returns a status line."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}, not {decision!r}")
    with open_graph() as graph:
        if not graph.get_state(thread(email_id)).next:  # not paused: nothing to resume
            return describe(email_id)
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO approvals (email_id, decision, decided_by) VALUES (%s, %s, %s)"
                " ON CONFLICT (email_id) DO NOTHING",
                (email_id, decision, by),
            )
            # Read back the stored decision: if someone else clicked first, theirs wins.
            decision, by = conn.execute(
                "SELECT decision, decided_by FROM approvals WHERE email_id = %s", (email_id,)
            ).fetchone()
        result = graph.invoke(Command(resume={"decision": decision, "by": by}), thread(email_id))
        status = save_outcome(email_id, result)
    return f"{email_id}: {status} ({PAST[decision]} by {by})"


def describe(email_id: str) -> str:
    """Why an email can't be decided now: unknown, never held, or already decided."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT e.status, a.decision, a.decided_by FROM emails e"
            " LEFT JOIN approvals a ON a.email_id = e.id WHERE e.id = %s", (email_id,)
        ).fetchone()
    if row is None:
        return f"{email_id}: no such email"
    status, decision, by = row
    already = f", already {PAST[decision]} by {by}" if decision else ""
    return f"{email_id}: not waiting for approval (status {status}{already})"


def pending() -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, sender, subject, hold_reasons, proposed_action, draft FROM emails"
            " WHERE status = 'awaiting_approval' ORDER BY received_at, id"
        ).fetchall()
    keys = ["id", "sender", "subject", "hold_reasons", "proposed_action", "draft"]
    return [dict(zip(keys, row, strict=True)) for row in rows]


def print_pending() -> None:
    rows = pending()
    if not rows:
        print("Nothing is waiting for approval.")
    for r in rows:
        print(f"{r['id']}  {r['sender']}  {r['subject']!r}")
        print(f"    held because: {', '.join(r['hold_reasons'])}")
        if r["proposed_action"]:
            print(f"    proposed action: {json.dumps(r['proposed_action'])}")
        print("    draft: " + r["draft"].replace("\n", "\n           "))
        print(f"    -> make approve ID={r['id']} DECISION=approve|reject\n")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Approve or reject held emails.")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("pending", help="list emails waiting for a person")
    d = sub.add_parser("decide", help="approve or reject one held email")
    d.add_argument("email_id")
    d.add_argument("decision", choices=DECISIONS)
    args = ap.parse_args(argv)
    if args.command == "pending":
        print_pending()
    else:
        print(decide(args.email_id, args.decision, by=f"cli:{getpass.getuser()}"))


if __name__ == "__main__":
    main()
