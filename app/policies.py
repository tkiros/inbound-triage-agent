"""Policy retrieval: BM25 over the handbook sections, using ParadeDB's pg_search.

`|||` is pg_search's "match any of these words" operator. It runs the query text through the
same tokenizer as the index (lowercase, English stemming, stop words dropped) instead of a query
parser, so quotes, colons, brackets or SQL in a customer's email are just more words. The text
is also passed as a bound parameter, so it never becomes part of the SQL.
"""

from __future__ import annotations

from common import db

# Words added to the query. Intent hints matter most for non-English emails, whose own words
# won't match an English handbook. Fact hints come from code (risk flags, verified amounts):
# a $249 refund should find "Refunds above $200" even though the email never says "200".
HINTS = {
    "order_status": "order status tracking shipped delivery",
    "refund": "refund return eligibility",
    "cancel_order": "cancel cancellation order",
    "billing": "charge payment invoice receipt",
    "account_update": "account update details address email",
    "product_question": "product warranty size stock",
    "phishing": "phishing suspicious credentials",
    "other": "",
    "legal_threat": "legal threats lawyers court",
    "chargeback": "chargeback payment dispute bank",
    "injection": "",
    "large_refund": "refund above $200 team lead review",
    "order_not_on_account": "discuss order account holder",
}


def search(text: str, k: int = 3) -> list[dict]:
    """The k best-matching handbook sections for `text`, best first."""
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT id, title, body, pdb.score(id) AS score
            FROM policy_sections
            WHERE title ||| %(q)s OR body ||| %(q)s
            ORDER BY score DESC, id
            LIMIT %(k)s
            """,
            {"q": text, "k": k},
        ).fetchall()
    return [{"id": r[0], "title": r[1], "body": r[2]} for r in rows]


def query_for(email: dict, tags: list[str]) -> str:
    """The search text for one email: hint words for its intent and facts, then its own words."""
    hints = " ".join(HINTS.get(tag, "") for tag in tags)
    return f"{hints} {email['subject']} {email['body']}"
