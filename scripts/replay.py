"""Post synthetic emails to the intake API, the way a mail provider's webhook would.

    python -m scripts.replay                     # all 300 emails (dev + test)
    python -m scripts.replay --split dev --limit 20
    python -m scripts.replay --ids e001,e002

Posting an email twice is safe: the second post is accepted and creates no job.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path

SETS = Path(__file__).resolve().parent.parent / "evals" / "sets"
DEFAULT_URL = "http://localhost:8001"


def load(split: str) -> list[dict]:
    """Eval rows (id, input, expected) for 'dev', 'test' or 'all'."""
    splits = ["dev", "test"] if split == "all" else [split]
    rows = []
    for name in splits:
        lines = (SETS / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
        rows += [json.loads(line) for line in lines if line.strip()]
    return sorted(rows, key=lambda r: r["id"])


def post(email: dict, url: str = DEFAULT_URL) -> dict:
    """POST one email to /inbound and return the JSON answer."""
    headers = {"Content-Type": "application/json"}
    if os.environ.get("INBOUND_TOKEN"):
        headers["X-Inbound-Token"] = os.environ["INBOUND_TOKEN"]
    request = urllib.request.Request(f"{url}/inbound", data=json.dumps(email).encode(),
                                     headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--split", choices=["dev", "test", "all"], default="all")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--ids", help="comma-separated email ids, e.g. e001,e002")
    ap.add_argument("--url", default=DEFAULT_URL)
    args = ap.parse_args(argv)

    rows = load(args.split)
    if args.ids:
        wanted = set(args.ids.split(","))
        rows = [r for r in rows if r["id"] in wanted]
    rows = rows[: args.limit] if args.limit else rows
    new = sum(post(r["input"], args.url)["queued"] for r in rows)
    print(f"posted {len(rows)} emails to {args.url}/inbound: {new} queued, "
          f"{len(rows) - new} duplicates")


if __name__ == "__main__":
    main()
