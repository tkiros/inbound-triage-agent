"""CRM tools: authorization is enforced inside the tool, with the envelope sender."""

import psycopg

from app import tools
from tests.helpers import crm_pick, someone_elses_order


def test_own_order_is_returned(test_db):
    sender, order_id, cents = crm_pick()
    result = tools.get_order(sender, order_id)
    assert result["ok"] is True
    assert result["order"]["id"] == order_id
    assert result["order"]["amount_cents"] == cents


def test_someone_elses_order_is_refused_like_a_missing_one(test_db):
    sender, _, _ = crm_pick()
    foreign = tools.get_order(sender, someone_elses_order(sender))
    missing = tools.get_order(sender, "LS-000000")
    assert foreign["ok"] is False and missing["ok"] is False
    assert foreign["error"] == missing["error"]  # nothing to learn about which IDs exist
    assert "order" not in foreign


def test_a_stranger_gets_no_order_at_all(test_db):
    _, order_id, _ = crm_pick()
    assert tools.get_order("stranger@example.org", order_id)["ok"] is False
    assert tools.get_customer("stranger@example.org") is None


def test_order_ids_are_normalized_and_sender_case_is_ignored(test_db):
    sender, order_id, _ = crm_pick()
    digits = order_id[3:]
    for written in (order_id.lower(), f"#{order_id}", f"LS{digits}", digits):
        assert tools.get_order(sender.upper(), written)["ok"] is True


def test_toolbox_binds_the_envelope_sender_whatever_the_model_asks(test_db):
    sender, own, _ = crm_pick()
    box = tools.ToolBox(sender)
    assert box.run("get_customer", {})["customer"]["email"] == sender
    assert box.run("get_order", {"order_id": own})["ok"] is True
    assert box.run("get_order", {"order_id": someone_elses_order(sender)})["ok"] is False
    assert box.run("delete_order", {"order_id": own})["ok"] is False
    assert [c["name"] for c in box.calls] == ["get_customer", "get_order", "get_order",
                                               "delete_order"]


def test_a_database_error_is_recorded_as_a_tool_error(monkeypatch):
    def broken(*args):
        raise psycopg.OperationalError("connection refused")
    monkeypatch.setattr(tools, "get_order", broken)
    box = tools.ToolBox("a@example.com")
    result = box.run("get_order", {"order_id": "LS-123456"})
    assert result == {"ok": False, "error": "Lookup failed; a person will follow up.",
                      "tool_error": True}
    assert box.calls[0]["result"]["tool_error"] is True


def test_tool_schemas_are_strict():
    for tool in tools.TOOLS:
        assert tool["strict"] is True
        assert tool["input_schema"]["additionalProperties"] is False
    get_customer = next(t for t in tools.TOOLS if t["name"] == "get_customer")
    assert get_customer["input_schema"]["properties"] == {}  # no identity for the model to pick
