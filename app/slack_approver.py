"""Slack approvals: held emails are posted with Approve / Reject buttons; a click resumes the run.

Socket Mode: this process opens a websocket to Slack and receives button clicks over it, so no
public URL is needed. Setup is in the README (create the app from slack/manifest.yml).

Environment:
    SLACK_BOT_TOKEN         xoxb-... (posting and updating messages)
    SLACK_APP_TOKEN         xapp-... (the Socket Mode connection)
    SLACK_APPROVAL_CHANNEL  channel ID the held emails are posted to, e.g. C0123456789
    SLACK_APPROVERS         optional: comma-separated Slack user IDs allowed to decide

Run with `make slack`. Without these variables, use the CLI: `make pending`, `make approve`.
"""

from __future__ import annotations

import json
import logging
import os

from app import approvals

log = logging.getLogger(__name__)

SLACK_TEXT_LIMIT = 2900  # a section block's text field holds at most 3000 characters


def configured() -> bool:
    return all(os.environ.get(k) for k in
               ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_APPROVAL_CHANNEL"))


def approval_blocks(payload: dict) -> list[dict]:
    """The Slack message for one held email: context, draft, and the two buttons."""
    action = json.dumps(payload["action"]) if payload["action"] else "none"
    summary = (
        f"*Held for approval* `{payload['email_id']}` from {payload['sender']}\n"
        f"*Subject:* {payload['subject']}\n"
        f"*Why:* {', '.join(payload['hold_reasons'])}\n"
        f"*Proposed action:* {action}"
    )
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": summary}},
        {"type": "section", "text": {"type": "mrkdwn",
                                     "text": "```" + payload["draft"][:SLACK_TEXT_LIMIT] + "```"}},
        {"type": "actions", "elements": [
            {"type": "button", "action_id": "approve", "style": "primary",
             "text": {"type": "plain_text", "text": "Approve"}, "value": payload["email_id"]},
            {"type": "button", "action_id": "reject", "style": "danger",
             "text": {"type": "plain_text", "text": "Reject"}, "value": payload["email_id"]},
        ]},
    ]


def post_for_approval(payload: dict, client=None) -> None:
    """Post one held email to the approval channel."""
    if client is None:
        from slack_sdk import WebClient
        client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
    client.chat_postMessage(channel=os.environ["SLACK_APPROVAL_CHANNEL"],
                            text=f"Email {payload['email_id']} is held for approval",
                            blocks=approval_blocks(payload))


def handle_click(ack, body: dict, client, decision: str) -> None:
    """Approve/Reject button handler. Acks first: Slack expects an answer within 3 seconds,
    and resuming the graph can take longer."""
    ack()
    email_id = body["actions"][0]["value"]
    user = body["user"]["id"]
    channel = body["channel"]["id"]
    allowed = [u.strip() for u in os.environ.get("SLACK_APPROVERS", "").split(",") if u.strip()]
    if allowed and user not in allowed:
        client.chat_postEphemeral(channel=channel, user=user,
                                  text="You're not on the approver list for triage emails.")
        return
    result = approvals.decide(email_id, decision, by=f"slack:{user}")
    client.chat_update(channel=channel, ts=body["message"]["ts"], text=result,
                       blocks=[{"type": "section", "text": {"type": "mrkdwn",
                                                            "text": f"*{result}*"}}])


def build_app():
    from slack_bolt import App

    app = App(token=os.environ["SLACK_BOT_TOKEN"])

    @app.action("approve")
    def on_approve(ack, body, client):
        handle_click(ack, body, client, "approve")

    @app.action("reject")
    def on_reject(ack, body, client):
        handle_click(ack, body, client, "reject")

    return app


def main() -> None:
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if not configured():
        raise SystemExit("Set SLACK_BOT_TOKEN, SLACK_APP_TOKEN and SLACK_APPROVAL_CHANNEL "
                         "(see README), or use `make pending` / `make approve` instead.")
    SocketModeHandler(build_app(), os.environ["SLACK_APP_TOKEN"]).start()


if __name__ == "__main__":
    main()
