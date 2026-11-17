"""The risk gate: every reason an email needs a person before anything is sent.

Deterministic rules come first. They look only at the email text and at CRM facts that CODE
looked up with the trusted sender, so they work the same however the model behaves. The
classifier's labels are a second opinion: they can add reasons, never remove one.

The keyword lists are plain phrases. Each one matches at the start of a word, so "refund"
also matches "refunded" and "refunds", but "court" doesn't match "courtesy" because the
phrase is "in court", not "court".
"""

from __future__ import annotations

import re

from app.tools import normalize_order_id

REFUND_REVIEW_CENTS = 20_000  # policy §2.3: refunds above $200 need a team lead

LEGAL = [
    "lawyer", "attorney", "legal action", "lawsuit", "sue you", "small claims", "in court",
    "to court", "consumer protection", "regulator", "avocat", "abogado", "acciones legales",
    "anwalt", "rechtliche schritte", "tribunal",
]
CHARGEBACK = [
    "chargeback", "charge back", "contracargo", "rückbuchung", "rétrofacturation",
    "reverse the charge", "dispute with my", "opened a dispute", "file a dispute",
    "filed a dispute", "dispute the charge",
]
INJECTION = [
    "ignore all previous instructions", "ignore previous instructions", "ignore your instructions",
    "ignore the above", "disregard your", "disregard all", "system override", "new instructions",
    "you are now", "pre-approved", "do not escalate", "</email>", "to the ai", "as an ai",
]
PHISHING = [
    "gift card", "wire transfer", "bank details have changed", "new bank details",
    "verify your account", "confirm your credentials",
]
PHISHING_NEAR_LINK = ["verif", "sign in", "sign-in", "log in", "login", "password", "credential",
                      "suspen"]
REFUND_WORDS = ["refund", "money back", "reembolso", "rembours", "rückerstattung",
                "geld zurück"]

ORDER_ID = re.compile(r"(?<![\w-])#?ls-?(\d{6})(?!\d)", re.IGNORECASE)
LINK = re.compile(r"https?://|www\.", re.IGNORECASE)


def normalize_text(text: str) -> str:
    """Lowercase and single-space the text, so phrases match across line breaks."""
    return re.sub(r"\s+", " ", text).lower()


def mentions(phrases: list[str], text: str) -> bool:
    """True if any phrase starts at a word boundary in the (normalized) text."""
    return any(re.search(r"(?<!\w)" + re.escape(p), text) for p in phrases)


def find_order_ids(text: str) -> list[str]:
    """Order IDs written as LS-123456, LS123456, #LS-123456 or ls-123456; first seen first."""
    ids = [normalize_order_id(digits) for digits in ORDER_ID.findall(text)]
    return list(dict.fromkeys(ids))


def looks_like_phishing(text: str) -> bool:
    return mentions(PHISHING, text) or (bool(LINK.search(text))
                                        and mentions(PHISHING_NEAR_LINK, text))


def text_flags(email: dict) -> list[str]:
    """Reasons found in the email's own words (subject and body)."""
    text = normalize_text(f"{email['subject']} {email['body']}")
    flags = []
    if mentions(LEGAL, text):
        flags.append("legal_threat")
    if mentions(CHARGEBACK, text):
        flags.append("chargeback")
    if mentions(INJECTION, text):
        flags.append("injection")
    if looks_like_phishing(text):
        flags.append("phishing")
    return flags


def wants_refund(email: dict, classification: dict) -> bool:
    text = normalize_text(f"{email['subject']} {email['body']}")
    return mentions(REFUND_WORDS, text) or classification["intent"] in ("refund", "cancel_order")


def propose_refund(classification: dict, orders: dict) -> dict | None:
    """The refund code would issue if the reply is sent, or None.

    Only code proposes moving money; the model has read-only tools. One verified order, delivered
    and not refunded before. Amounts above the review limit are still proposed: the gate holds
    them, and a person approves or rejects the refund together with the reply.
    """
    # ponytail: no delivery date in the mock CRM, so §2.1's 30-day window isn't checked.
    if classification["intent"] != "refund":
        return None
    own = [r["order"] for r in orders.values() if r["ok"]]
    if len(own) != 1 or own[0]["status"] != "delivered" or own[0]["refund"] is not None:
        return None
    return {"type": "refund", "order_id": own[0]["id"], "amount_cents": own[0]["amount_cents"]}


def section_id(raw: str) -> str:
    """'2.3', '§ 2.3' and '§2.3' all mean §2.3."""
    return "§" + re.sub(r"[^\d.]", "", raw)


def draft_problems(state: dict) -> list[str]:
    """Checks on the draft itself: grounded in retrieved policy, no unverified order IDs."""
    problems = []
    cited = {section_id(s) for s in state["draft"]["cited_sections"]}
    retrieved = {p["id"] for p in state["policies"]}
    if not cited:
        problems.append("no_citation")
    elif not cited <= retrieved:
        problems.append("cites_unretrieved_section")
    verified = {oid for oid, r in state["orders"].items() if r["ok"]}
    verified |= {c["result"]["order"]["id"] for c in state["tool_calls"]
                 if c["name"] == "get_order" and c["result"]["ok"]}
    if set(find_order_ids(state["draft"]["reply"])) - verified:
        problems.append("draft_mentions_unverified_order")
    return problems


def hold_reasons(state: dict) -> list[str]:
    """Every reason this email needs a person. An empty list means it may be sent automatically.

    Uses these state keys: email, classification, customer, orders, tool_calls, policies, draft.
    """
    email, c = state["email"], state["classification"]
    reasons = text_flags(email)

    customer = state["customer"]
    if customer and customer["tier"] == "vip":
        reasons.append("vip_customer")  # policy §8.1
    refused = [oid for oid, r in state["orders"].items() if not r["ok"]]
    refused += [call["input"]["order_id"] for call in state["tool_calls"]
                if call["name"] == "get_order" and not call["result"]["ok"]]
    if refused:
        reasons.append("order_not_on_account")
    big = [r for r in state["orders"].values()
           if r["ok"] and r["order"]["amount_cents"] > REFUND_REVIEW_CENTS]
    if big and wants_refund(email, c):
        reasons.append("large_refund")
    if any(call["result"].get("tool_error") for call in state["tool_calls"]):
        reasons.append("tool_error")
    reasons += draft_problems(state)

    # Second opinion from the classifier: can only add reasons.
    if c["risk"] == "high":
        reasons.append("classifier_risk_high")
    if c["intent"] == "phishing":
        reasons.append("classifier_phishing")
    if c["intent"] == "other":
        reasons.append("intent_unclear")
    return list(dict.fromkeys(reasons))
