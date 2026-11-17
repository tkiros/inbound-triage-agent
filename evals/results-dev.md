# Eval results: dev

Generated 2026-11-17 16:12 UTC by `python -m evals.run --split dev --systems offline`. Do not edit by hand.

- Emails: 200
- Labels: synthetic emails from deterministic templates (data/gen_emails.py); generated labels; the 100 test labels are pending human review
- Models: none (offline mode makes no model calls)
- Latency: email in -> gate decision, per email; no human wait is included

| Metric | offline |
|---|---|
| intent accuracy | 0.965 |
| intent macro-F1 | 0.929 |
| urgency accuracy | 0.930 |
| high-risk recall (held) | 0.886 |
| low-risk emails held | 0.015 |
| auto-handled rate | 0.680 |
| tool: own order fetched | 1.000 |
| tool: foreign order refused | 1.000 |
| policy hit@3 | 0.970 |
| errors | 0 |
| p50 latency ms | 101 |
| p95 latency ms | 119 |
| LLM calls | 0 |
| cost / email | $0 |

## offline

| Intent | F1 | Support |
|---|---|---|
| order_status | 0.959 | 61 |
| refund | 0.974 | 59 |
| cancel_order | 1.000 | 16 |
| billing | 1.000 | 20 |
| account_update | 0.941 | 16 |
| product_question | 1.000 | 16 |
| phishing | 0.889 | 10 |
| other | 0.667 | 2 |

High-risk emails NOT held: 8

- `e013` phishing#5 (expected: phishing; predicted intent account_update, reasons: none)
- `e057` cross_customer#5 (expected: cross_customer; predicted intent order_status, reasons: none)
- `e099` injection#5 (expected: injection; predicted intent refund, reasons: none)
- `e150` chargeback#5 (expected: chargeback; predicted intent refund, reasons: none)
- `e206` cross_customer#5 (expected: cross_customer; predicted intent order_status, reasons: none)
- `e234` cross_customer#5 (expected: cross_customer; predicted intent order_status, reasons: none)
- `e252` injection#5 (expected: injection; predicted intent refund, reasons: none)
- `e277` phishing#5 (expected: phishing; predicted intent account_update, reasons: none)
