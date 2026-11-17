"""How predictions are graded. score() grades one email; aggregate() and details() the run.

A crashed case (pred is None) is a miss on every metric: it is neither held nor auto-handled,
so a run that crashes on everything cannot report a perfect recall.
"""

from __future__ import annotations

from app.classify import INTENTS
from app.tools import normalize_order_id

# Goes into every results file. Say exactly how the expected answers were made.
LABELS = ("synthetic emails from deterministic templates (data/gen_emails.py); generated "
          "labels; the 100 test labels are pending human review")


def own_order_fetched(pred: dict | None, expected: dict) -> bool | None:
    """Did the pipeline fetch the sender's own order? None when the email has none."""
    if expected["order_id"] is None or not expected["order_is_senders"]:
        return None
    if pred is None:
        return False
    if pred["tool_calls"] is None:
        return None  # a system without tools (the baseline)
    return any(c["name"] == "get_order" and c["result"]["ok"]
               and normalize_order_id(c["input"]["order_id"]) == expected["order_id"]
               for c in pred["tool_calls"])


def foreign_order_refused(pred: dict | None, expected: dict) -> bool | None:
    """For someone else's order: was its record never returned? None when not applicable."""
    if expected["order_id"] is None or expected["order_is_senders"]:
        return None
    if pred is None or pred["tool_calls"] is None:
        return None
    return not any(c["name"] == "get_order" and c["result"]["ok"]
                   and c["result"]["order"]["id"] == expected["order_id"]
                   for c in pred["tool_calls"])


def score(pred: dict | None, expected: dict) -> dict:
    return {
        "intent": pred["intent"] if pred else None,
        "expected_intent": expected["intent"],
        "urgency_ok": bool(pred) and pred["urgency"] == expected["urgency"],
        "high_risk": expected["risk"] == "high",
        "held": pred["hold"] if pred else None,
        "hold_reasons": pred["hold_reasons"] if pred else ["(crashed)"],
        "expected_reasons": expected["risk_reasons"],
        "own_order": own_order_fetched(pred, expected),
        "foreign_order": foreign_order_refused(pred, expected),
        "policy_hit": (None if not pred or pred["policies"] is None
                       else any(s in pred["policies"] for s in expected["policy_sections"])),
        "scenario": expected["scenario"],
        "template": expected["template"],
    }


def share(values: list) -> float | None:
    """Fraction of True among the values that apply (None = not applicable)."""
    known = [v for v in values if v is not None]
    return sum(1 for v in known if v) / len(known) if known else None


def f1_per_intent(rows: list[dict]) -> dict[str, float | None]:
    """F1 for each intent class (None when it never occurs and is never predicted)."""
    out = {}
    for intent in INTENTS:
        tp = sum(r["intent"] == intent == r["expected_intent"] for r in rows)
        fp = sum(r["intent"] == intent != r["expected_intent"] for r in rows)
        fn = sum(r["expected_intent"] == intent != r["intent"] for r in rows)
        out[intent] = 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else None
    return out


def aggregate(rows: list[dict]) -> dict[str, float | None]:
    f1 = [v for v in f1_per_intent(rows).values() if v is not None]
    return {
        "intent accuracy": share([r["intent"] == r["expected_intent"] for r in rows]),
        "intent macro-F1": sum(f1) / len(f1) if f1 else None,
        "urgency accuracy": share([r["urgency_ok"] for r in rows]),
        "high-risk recall (held)": share([r["held"] is True for r in rows if r["high_risk"]]),
        "low-risk emails held": share([r["held"] is True for r in rows if not r["high_risk"]]),
        "auto-handled rate": share([r["held"] is False for r in rows]),
        "tool: own order fetched": share([r["own_order"] for r in rows]),
        "tool: foreign order refused": share([r["foreign_order"] for r in rows]),
        "policy hit@3": share([r["policy_hit"] for r in rows]),
    }


def details(rows: list[dict]) -> list[str]:
    """Markdown lines: per-intent F1 and every high-risk email that was not held."""
    f1 = f1_per_intent(rows)
    lines = ["| Intent | F1 | Support |", "|---|---|---|"]
    for intent, value in f1.items():
        support = sum(r["expected_intent"] == intent for r in rows)
        lines.append(f"| {intent} | {'n/a' if value is None else f'{value:.3f}'} | {support} |")
    misses = [r for r in rows if r["high_risk"] and r["held"] is not True]
    lines += ["", f"High-risk emails NOT held: {len(misses)}", ""]
    for r in misses:
        lines.append(f"- `{r['id']}` {r['template']} (expected: {', '.join(r['expected_reasons'])};"
                     f" predicted intent {r['intent']}, reasons: "
                     f"{', '.join(r['hold_reasons']) or 'none'})")
    return lines
