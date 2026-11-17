"""The synthetic data is deterministic, and the committed files match the generator."""

import json
from collections import Counter

from data import crm_gen, gen_emails, seed


def test_crm_is_the_same_every_run():
    assert crm_gen.generate() == crm_gen.generate()
    customers, orders, _ = crm_gen.generate()
    assert len(customers) == 500 and len(orders) == 2000
    assert len({o[0] for o in orders}) == 2000  # order IDs are unique


def test_committed_email_sets_match_the_generator():
    emails = gen_emails.generate()
    for split in ("dev", "test"):
        committed = [json.loads(line) for line in
                     (gen_emails.OUT / f"{split}.jsonl").read_text(encoding="utf-8").splitlines()]
        expected = [{"id": e["id"], "input": e["input"], "expected": e["expected"]}
                    for e in emails if e["split"] == split]
        assert committed == expected, f"run `make data`: {split}.jsonl is stale"


def test_split_sizes_and_every_hard_case_in_both_splits():
    emails = gen_emails.generate()
    splits = Counter(e["split"] for e in emails)
    assert splits == {"dev": 200, "test": 100}
    for scenario in gen_emails.SCENARIOS:
        in_splits = {e["split"] for e in emails if e["expected"]["scenario"] == scenario}
        assert in_splits == {"dev", "test"}, scenario


def test_labels_are_consistent():
    for e in gen_emails.generate():
        x = e["expected"]
        assert (x["risk"] == "high") == bool(x["risk_reasons"])
        if x["scenario"] == "cross_customer":
            assert x["order_is_senders"] is False
        assert e["input"]["id"] == e["id"]


def test_handbook_has_25_numbered_sections():
    sections = seed.handbook_sections(seed.HANDBOOK.read_text(encoding="utf-8"))
    assert len(sections) == 25
    assert sections[0][0] == "§1.1" and sections[-1][0] == "§8.1"
    assert all(body and "##" not in body for _, _, body in sections)
