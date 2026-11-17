"""End-to-end demo: seed, start the API, replay a few emails, run the worker, approve one.

    make demo      (needs `make up` first)

Without an API key this runs in OFFLINE mode: a keyword classifier and template drafts stand in
for the model, so the plumbing (queue, tools, gate, approval, outbox) can be shown for free.
With ANTHROPIC_API_KEY set, the classifier and the drafter are the real model.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
import urllib.request

from app import approvals, worker
from app.graph import offline_mode
from common import db
from data import seed
from scripts import replay

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOW = ["order_status", "small_refund", "large_refund", "legal", "injection"]


def step(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 70 - len(title)))


def demo_emails() -> list[dict]:
    """The first non-evasive dev email of each scenario in SHOW."""
    rows = replay.load("dev")
    return [next(r for r in rows if r["expected"]["scenario"] == s and not r["expected"]["evasive"])
            for s in SHOW]


def start_api() -> subprocess.Popen:
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "8001", "--log-level",
         "warning"], cwd=ROOT)
    for _ in range(50):
        try:
            with urllib.request.urlopen(f"{replay.DEFAULT_URL}/health", timeout=1):
                return api
        except OSError:
            time.sleep(0.2)
    api.terminate()
    raise SystemExit("the API did not start on port 8001")


def main() -> None:
    # stdout for logs too, so they interleave with the step headers in order
    logging.basicConfig(level=logging.INFO, format="    [%(name)s] %(message)s",
                        stream=sys.stdout)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if offline_mode():
        print("OFFLINE MODE: no ANTHROPIC_API_KEY, so a keyword classifier and template drafts "
              "stand in for the model.\nEverything else (queue, tools, risk gate, approval, "
              "outbox) is the real code path.")
    else:
        print("LLM MODE: the classifier and drafter call the model (this costs money).")

    step("1. Reset the database (make seed)")
    print("    " + str(seed.seed(os.environ["DATABASE_URL"])))

    step("2. Start the intake API on http://localhost:8001")
    api = start_api()
    try:
        step("3. Replay 5 emails to POST /inbound (scripts/replay.py)")
        emails = demo_emails()
        for row in emails:
            answer = replay.post(row["input"])
            print(f"    {row['id']}  {row['expected']['scenario']:<13} queued={answer['queued']}"
                  f"  {row['input']['subject']!r}")
        again = replay.post(emails[0]["input"])
        print(f"    {emails[0]['id']} posted again -> duplicate={again['duplicate']} (no new job)")

        step("4. Run the worker until the queue is empty (make worker)")
        worker.run(once=True)

        step("5. What happened to each email")
        with db.connect() as conn:
            for row in emails:
                status, reasons = conn.execute(
                    "SELECT status, hold_reasons FROM emails WHERE id = %s", (row["id"],)
                ).fetchone()
                print(f"    {row['id']}  {row['expected']['scenario']:<13} {status:<18}"
                      f" {', '.join(reasons) or '-'}")
            auto = next(r for r in emails if r["expected"]["scenario"] == "order_status")
            sent = conn.execute("SELECT to_address, subject, body FROM outbox WHERE email_id = %s",
                                (auto["id"],)).fetchone()
        print(f"\n    Auto-handled reply for {auto['id']} (outbox row):")
        print(f"    To: {sent[0]}\n    Subject: {sent[1]}\n")
        print("    " + sent[2].replace("\n", "\n    "))

        step("6. Emails waiting for a person (make pending)")
        approvals.print_pending()

        held = next(r for r in emails if r["expected"]["scenario"] == "large_refund")
        step(f"7. Approve {held['id']} from the CLI, twice (make approve ID={held['id']} ...)")
        for _ in range(2):
            out = subprocess.run([sys.executable, "-m", "app.approvals", "decide", held["id"],
                                  "approve"], cwd=ROOT, capture_output=True, text=True,
                                 check=True)
            print(f"    $ make approve ID={held['id']} DECISION=approve\n"
                  f"    {out.stdout.strip()}")

        step(f"8. The outbox and refund rows for {held['id']}: exactly one of each")
        with db.connect() as conn:
            for table in ("outbox", "refunds"):
                cols = "email_id, to_address, subject" if table == "outbox" \
                    else "email_id, order_id, amount_cents, status"
                found = conn.execute(f"SELECT {cols} FROM {table} WHERE email_id = %s",
                                     (held["id"],)).fetchall()
                print(f"    {table}: {found}")
    finally:
        api.terminate()
        api.wait(timeout=10)


if __name__ == "__main__":
    main()
