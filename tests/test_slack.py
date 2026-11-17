"""Slack approver logic with mocks. No Slack workspace is contacted (none is available here)."""

from unittest.mock import MagicMock

from app import slack_approver

PAYLOAD = {"email_id": "e042", "sender": "a@example.com", "subject": "Refund",
           "hold_reasons": ["large_refund"], "action": {"type": "refund", "order_id": "LS-1",
                                                        "amount_cents": 24900},
           "draft": "Hi, ..."}


def click(action_id="approve", user="U1") -> dict:
    return {"actions": [{"action_id": action_id, "value": "e042"}], "user": {"id": user},
            "channel": {"id": "C9"}, "message": {"ts": "171.0"}}


def test_message_has_context_and_both_buttons():
    blocks = slack_approver.approval_blocks(PAYLOAD)
    assert "large_refund" in blocks[0]["text"]["text"]
    assert "LS-1" in blocks[0]["text"]["text"]
    buttons = blocks[-1]["elements"]
    assert [(b["action_id"], b["value"]) for b in buttons] == [("approve", "e042"),
                                                               ("reject", "e042")]


def test_post_for_approval_uses_the_configured_channel(monkeypatch):
    monkeypatch.setenv("SLACK_APPROVAL_CHANNEL", "C9")
    client = MagicMock()
    slack_approver.post_for_approval(PAYLOAD, client=client)
    kwargs = client.chat_postMessage.call_args.kwargs
    assert kwargs["channel"] == "C9" and kwargs["blocks"][-1]["type"] == "actions"


def test_click_acks_first_then_resumes_the_run(monkeypatch):
    order = []
    monkeypatch.setattr(slack_approver.approvals, "decide",
                        lambda email_id, decision, by: order.append(("decide", email_id, decision,
                                                                     by)) or "e042: sent")
    client = MagicMock()
    slack_approver.handle_click(lambda: order.append(("ack",)), click(), client, "approve")
    assert order == [("ack",), ("decide", "e042", "approve", "slack:U1")]
    update = client.chat_update.call_args.kwargs
    assert update["channel"] == "C9" and update["ts"] == "171.0"
    assert "e042: sent" in update["text"]


def test_only_listed_approvers_can_decide(monkeypatch):
    monkeypatch.setenv("SLACK_APPROVERS", "U1, U2")
    decide = MagicMock()
    monkeypatch.setattr(slack_approver.approvals, "decide", decide)
    client = MagicMock()
    ack = MagicMock()
    slack_approver.handle_click(ack, click(user="U666"), client, "approve")
    ack.assert_called_once()
    decide.assert_not_called()
    client.chat_postEphemeral.assert_called_once()
    slack_approver.handle_click(ack, click(user="U2"), client, "reject")
    decide.assert_called_once_with("e042", "reject", by="slack:U2")


def test_configured_needs_all_three_settings(monkeypatch):
    for key in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_APPROVAL_CHANNEL"):
        monkeypatch.setenv(key, "x")
    assert slack_approver.configured()
    monkeypatch.delenv("SLACK_APP_TOKEN")
    assert not slack_approver.configured()
