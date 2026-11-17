# Eval results: test

Generated 2026-11-17 16:12 UTC by `python -m evals.run --split test --systems offline`. Do not edit by hand.

- Emails: 100
- Labels: synthetic emails from deterministic templates (data/gen_emails.py); generated labels; the 100 test labels are pending human review
- Models: none (offline mode makes no model calls)
- Latency: email in -> gate decision, per email; no human wait is included

| Metric | offline |
|---|---|
| intent accuracy | 0.970 |
| intent macro-F1 | 0.949 |
| urgency accuracy | 0.910 |
| high-risk recall (held) | 0.914 |
| low-risk emails held | 0.015 |
| auto-handled rate | 0.670 |
| tool: own order fetched | 1.000 |
| tool: foreign order refused | 1.000 |
| policy hit@3 | 0.970 |
| errors | 0 |
| p50 latency ms | 108 |
| p95 latency ms | 154 |
| LLM calls | 0 |
| cost / email | $0 |

## offline

| Intent | F1 | Support |
|---|---|---|
| order_status | 0.966 | 29 |
| refund | 0.984 | 31 |
| cancel_order | 1.000 | 7 |
| billing | 1.000 | 8 |
| account_update | 0.952 | 10 |
| product_question | 1.000 | 8 |
| phishing | 0.889 | 5 |
| other | 0.800 | 2 |

High-risk emails NOT held: 3

- `e215` chargeback#5 (expected: chargeback; predicted intent refund, reasons: none)
- `e240` phishing#5 (expected: phishing; predicted intent account_update, reasons: none)
- `e258` injection#5 (expected: injection; predicted intent refund, reasons: none)
