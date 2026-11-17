"""Eval harness.  python -m evals.run --split dev [--limit N] [--systems baseline,pipeline,offline]

Runs each system in app/pipeline.py over evals/sets/<split>.jsonl, grades with
evals/score.py, and writes evals/results-<split>.md: metrics, p50/p95 latency per email, LLM
cost measured from llm_calls, per-intent F1 and every high-risk miss. Tune on dev; run test
once, when you've stopped changing things, and report test.

Latency is the time from email to gate decision. The eval never waits for a human.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from app import pipeline
from common import db, llm
from evals import score as scoring

HERE = Path(__file__).parent
RESULTS_DIR = HERE
SYSTEMS = {"baseline": pipeline.baseline, "pipeline": pipeline.predict,
           "offline": pipeline.offline}
NEEDS_MODEL = {"baseline", "pipeline"}


def load(split: str, limit: int | None) -> list[dict]:
    lines = (HERE / "sets" / f"{split}.jsonl").read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines if line.strip()]
    if not rows:
        raise SystemExit(f"evals/sets/{split}.jsonl is empty")
    return rows[:limit] if limit else rows


def pct(values: list[float], p: float) -> float:
    """Nearest-rank percentile."""
    s = sorted(values)
    return s[max(0, math.ceil(p * len(s)) - 1)]


def run_system(fn, rows: list[dict]) -> tuple[list[dict], list[float], int]:
    """Run fn on every case: per-case scores (with ids), latencies in ms, and the error count."""
    per_case, latencies, errors = [], [], 0
    for row in rows:
        t0 = time.perf_counter()
        try:
            pred = fn(row["input"])
        except Exception:  # a crashed case is a failed case, not a crashed eval
            errors += 1
            pred = None
            print(f"[{row.get('id', '?')}] error:\n{traceback.format_exc()}", file=sys.stderr)
        latencies.append((time.perf_counter() - t0) * 1000)
        per_case.append({"id": row.get("id", "?"), **scoring.score(pred, row["expected"])})
    return per_case, latencies, errors


def llm_usage(run_id: str) -> tuple[int, float | None, int] | None:
    """(calls, total cost, calls with unknown cost) for one run; None without a database."""
    if not os.environ.get("DATABASE_URL"):
        return None
    try:
        with db.connect() as conn:
            return conn.execute(
                "SELECT count(*), sum(cost_usd), count(*) FILTER (WHERE cost_usd IS NULL)"
                " FROM llm_calls WHERE run_id = %s",
                (run_id,),
            ).fetchone()
    except psycopg.Error as e:  # the paid-for results still get written
        print(f"cost lookup skipped, database unreachable: {e}".strip(), file=sys.stderr)
        return None


def fmt_usage(usage, n: int) -> tuple[str, str]:
    if usage is None:
        return "n/a", "n/a (no database)"
    calls, total, unknown = usage
    if calls == 0:
        return "0", "$0"
    cost = "unknown" if total is None else f"${float(total) / n:.6f}"
    if unknown and total is not None:
        cost += f" (+{unknown} unpriced calls)"
    return str(calls), cost


def fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--split", choices=["dev", "test"], default="dev")
    ap.add_argument("--limit", type=int, help="first N cases only (CI smoke run)")
    ap.add_argument("--systems", default="baseline,pipeline,offline")
    args = ap.parse_args(argv)

    rows = load(args.split, args.limit)
    names = args.systems.split(",")
    stamp = datetime.now(UTC)
    results, total_errors = [], 0
    for name in names:
        run_id = f"{name}-{args.split}-{stamp:%Y%m%dT%H%M%SZ}"
        os.environ["EVAL_RUN"] = run_id  # common/llm.py tags llm_calls rows with it
        try:
            per_case, latencies, errors = run_system(SYSTEMS[name], rows)
        finally:
            os.environ.pop("EVAL_RUN", None)
        total_errors += errors
        results.append((name, per_case, scoring.aggregate(per_case), latencies, errors,
                        llm_usage(run_id)))

    models = (f"small=`{llm.MODELS['small']}`, large=`{llm.MODELS['large']}`"
              if NEEDS_MODEL & set(names) else "none (offline mode makes no model calls)")
    lines = [
        f"# Eval results: {args.split}",
        "",
        f"Generated {stamp:%Y-%m-%d %H:%M} UTC by `python -m evals.run --split {args.split}"
        f" --systems {args.systems}`" + (f" --limit {args.limit}" if args.limit else "")
        + ". Do not edit by hand.",
        "",
        f"- Emails: {len(rows)}" + (f" (first {args.limit} only)" if args.limit else ""),
        f"- Labels: {scoring.LABELS}",
        f"- Models: {models}",
        "- Latency: email in -> gate decision, per email; no human wait is included",
        "",
        "| Metric | " + " | ".join(r[0] for r in results) + " |",
        "|---" * (len(results) + 1) + "|",
    ]
    for metric in results[0][2]:
        lines.append(f"| {metric} | " + " | ".join(fmt(r[2][metric]) for r in results) + " |")
    usage = [fmt_usage(r[5], len(rows)) for r in results]
    lines += [
        "| errors | " + " | ".join(str(r[4]) for r in results) + " |",
        "| p50 latency ms | " + " | ".join(f"{pct(r[3], 0.5):.0f}" for r in results) + " |",
        "| p95 latency ms | " + " | ".join(f"{pct(r[3], 0.95):.0f}" for r in results) + " |",
        "| LLM calls | " + " | ".join(u[0] for u in usage) + " |",
        "| cost / email | " + " | ".join(u[1] for u in usage) + " |",
    ]
    for name, per_case, *_ in results:
        lines += ["", f"## {name}", "", *scoring.details(per_case)]

    out = RESULTS_DIR / f"results-{args.split}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out.read_text(encoding="utf-8"))
    return 1 if total_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
