"""The eval harness and the metrics, plus one end-to-end eval run against the fake API."""

from evals import run, score
from tests.helpers import install_fake_api, plausible_model


def test_pct_nearest_rank():
    assert run.pct([10, 20, 30, 40], 0.5) == 20
    assert run.pct([10, 20, 30, 40], 0.95) == 40
    assert run.pct([5], 0.95) == 5


EXPECTED = {"intent": "refund", "urgency": "normal", "risk": "high",
            "risk_reasons": ["large_refund"], "order_id": "LS-1", "order_is_senders": True,
            "policy_sections": ["§2.3"], "scenario": "large_refund", "template": "t#1"}


def test_crashed_case_is_an_error_and_a_miss_on_every_metric():
    def boom(_):
        raise ValueError("boom")

    per_case, latencies, errors = run.run_system(boom, [{"id": "a", "input": {},
                                                         "expected": EXPECTED}])
    assert errors == 1 and len(latencies) == 1
    metrics = score.aggregate(per_case)
    assert metrics["high-risk recall (held)"] == 0.0  # a crash is not a hold
    assert metrics["auto-handled rate"] == 0.0  # ...and not an automatic reply either
    assert metrics["intent accuracy"] == 0.0


def test_metrics_on_hand_made_predictions():
    held = {"intent": "refund", "urgency": "normal", "hold": True, "hold_reasons": ["x"],
            "tool_calls": [{"name": "get_order", "input": {"order_id": "ls-1"},
                            "result": {"ok": True, "order": {"id": "LS-1"}}}],
            "policies": ["§2.1", "§2.3"], "action": None}
    rows = [{"id": "a", **score.score(held, EXPECTED)},
            {"id": "b", **score.score({**held, "intent": "billing", "hold": False,
                                       "tool_calls": [], "policies": ["§4.1"]}, EXPECTED)}]
    m = score.aggregate(rows)
    assert m["intent accuracy"] == 0.5
    assert m["high-risk recall (held)"] == 0.5
    assert m["tool: own order fetched"] == 0.5
    assert m["tool: foreign order refused"] is None  # no foreign orders in these cases
    assert m["policy hit@3"] == 0.5
    f1 = score.f1_per_intent(rows)
    assert f1["refund"] == 2 / 3 and f1["billing"] == 0.0 and f1["phishing"] is None
    assert any("`b`" in line for line in score.details(rows))  # the miss is listed


def test_baseline_tool_metrics_are_not_applicable():
    pred = {"intent": "refund", "urgency": "normal", "hold": True, "hold_reasons": [],
            "tool_calls": None, "policies": None, "action": None}
    s = score.score(pred, EXPECTED)
    assert s["own_order"] is None and s["policy_hit"] is None


def test_full_eval_runs_end_to_end_against_the_fake_api(test_db, monkeypatch, tmp_path, query):
    install_fake_api(monkeypatch, plausible_model)
    monkeypatch.setattr(run, "RESULTS_DIR", tmp_path)
    exit_code = run.main(["--split", "dev", "--limit", "12",
                          "--systems", "baseline,pipeline,offline"])
    assert exit_code == 0
    report = (tmp_path / "results-dev.md").read_text()
    assert "| Metric | baseline | pipeline | offline |" in report
    assert "| tool: own order fetched | n/a |" in report  # the baseline has no tools
    assert "| errors | 0 | 0 | 0 |" in report
    # Cost is measured from the llm_calls rows the run wrote (fake usage: 1000 in / 100 out).
    assert table_row(report, "LLM calls") == ["12", "36", "0"]  # pipeline: classify + 2 draft turns
    assert table_row(report, "cost / email") == ["$0.000150", "$0.000450", "$0"]
    run_id = query("SELECT max(run_id) FROM llm_calls WHERE run_id LIKE 'pipeline-dev-%%'")[0][0]
    steps = query("SELECT step, count(*) FROM llm_calls WHERE run_id = %s GROUP BY step"
                  " ORDER BY step", (run_id,))
    assert steps == [("classify", 12), ("draft", 24)]


def table_row(report: str, name: str) -> list[str]:
    line = next(line for line in report.splitlines() if line.startswith(f"| {name} |"))
    return [cell.strip() for cell in line.strip("|").split("|")][1:]
