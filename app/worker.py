"""The worker: claim a job, run the email through the graph, record what happened.

    python -m app.worker          # poll forever (make worker)
    python -m app.worker --once   # drain the queue, then exit (used by make demo)

Run as many workers as you like: the queue's SKIP LOCKED claim never gives a job to two.
"""

from __future__ import annotations

import argparse
import logging
import time

from app import jobs, slack_approver
from app.graph import offline_mode, open_graph, save_outcome, thread

log = logging.getLogger(__name__)
POLL_SECONDS = 1.0


def notify_approvers(payload: dict) -> None:
    """Tell a person an email is waiting. If Slack is down, the email simply stays held."""
    email_id = payload["email_id"]
    if slack_approver.configured():
        try:
            slack_approver.post_for_approval(payload)
            return
        except Exception:  # any Slack failure: never lose the hold, fall back to the CLI
            log.exception("could not post %s to Slack", email_id)
    log.info("HELD %s (%s): run `make approve ID=%s DECISION=approve|reject`",
             email_id, ", ".join(payload["hold_reasons"]), email_id)


def process(graph, job_id: int, email_id: str) -> None:
    email = jobs.load_email(email_id)
    try:
        result = graph.invoke({"email": email, "offline": offline_mode()}, thread(email_id))
    except Exception as e:  # model down after SDK retries, bad reply, DB error: a person takes over
        log.exception("email %s failed; marked needs_human", email_id)
        jobs.set_email_status(email_id, "needs_human")
        jobs.finish(job_id, "failed", repr(e))
        return
    status = save_outcome(email_id, result)
    if "__interrupt__" in result:
        notify_approvers(result["__interrupt__"][0].value)
    else:
        log.info("email %s: %s automatically", email_id, status)
    jobs.finish(job_id, "done")


def run(once: bool = False) -> int:
    """Process jobs until the queue is empty (once=True) or forever. Returns jobs processed."""
    done = 0
    with open_graph() as graph:
        while True:
            job = jobs.claim()
            if job is None:
                if once:
                    return done
                time.sleep(POLL_SECONDS)
                continue
            process(graph, *job)
            done += 1


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true", help="exit when the queue is empty")
    args = ap.parse_args()
    mode = "OFFLINE (keyword classifier + template drafts)" if offline_mode() else "LLM"
    log.info("worker starting in %s mode", mode)
    n = run(once=args.once)
    log.info("processed %d jobs", n)


if __name__ == "__main__":
    main()
