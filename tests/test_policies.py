"""BM25 policy search over the handbook (pg_search)."""

import pytest

from app import policies

ODD_INPUTS = [
    "",
    "   ",
    "!!! ??? ...",
    'title:(" AND OR ) ^~* \\',
    "refund'); DROP TABLE policy_sections; --",
    "Rückerstattung für Bestellung — ¿dónde está mi pedido? 🙂",
    "a" * 20_000,
    "\x00".replace("\x00", "") + "line1\nline2\ttab",
]


@pytest.mark.parametrize("text", ODD_INPUTS)
def test_odd_customer_text_never_breaks_the_query(test_db, query, text):
    results = policies.search(text)
    assert isinstance(results, list) and len(results) <= 3
    assert query("SELECT count(*) FROM policy_sections")[0][0] == 25  # table untouched


def test_finds_the_relevant_section(test_db):
    assert policies.search("Is my parcel lost? Tracking says shipped 3 weeks ago")[0]["id"] in {
        "§1.3", "§1.4", "§1.2"}
    assert policies.search("I will file a chargeback with my bank")[0]["id"] == "§4.3"
    assert "§2.3" in [p["id"] for p in policies.search(policies.HINTS["large_refund"])]


def test_stemming_matches_word_forms(test_db):
    ids = [p["id"] for p in policies.search("refunding")]
    assert any(i.startswith("§2.") for i in ids)
