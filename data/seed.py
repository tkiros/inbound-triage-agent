"""Reset the database to a known state: schema, mock CRM, policy handbook, empty pipeline.

    python -m data.seed      (make seed)

Runs every db/init/*.sql file (they are idempotent), so it also works on an existing volume
and in CI. Deletes all emails, jobs, approvals, outbox rows and graph checkpoints: dev only.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver

from data import crm_gen

ROOT = Path(__file__).resolve().parent.parent
HANDBOOK = ROOT / "data" / "policies.md"


def handbook_sections(text: str) -> list[tuple[str, str, str]]:
    """(id, title, body) for every '### §x.y Title' section of the handbook."""
    sections = []
    for block in re.split(r"^### ", text, flags=re.MULTILINE)[1:]:
        heading, _, body = block.partition("\n")
        section_id, _, title = heading.partition(" ")
        body = body.split("\n## ")[0]  # stop at the next chapter heading
        sections.append((section_id, title.strip(), " ".join(body.split())))
    return sections


def seed(url: str) -> dict[str, int]:
    customers, orders, refunds = crm_gen.generate()
    policies = handbook_sections(HANDBOOK.read_text(encoding="utf-8"))
    with psycopg.connect(url, autocommit=True) as conn:
        for sql_file in sorted((ROOT / "db" / "init").glob("*.sql")):
            conn.execute(sql_file.read_text())
        with conn.transaction(), conn.cursor() as cur:
            cur.execute("TRUNCATE outbox, approvals, jobs, emails, refunds, orders, customers,"
                        " policy_sections RESTART IDENTITY CASCADE")
            cur.executemany("INSERT INTO customers (id, email, name, tier) VALUES (%s,%s,%s,%s)",
                            customers)
            cur.executemany("INSERT INTO orders (id, customer_id, status, amount_cents, item,"
                            " ordered_on) VALUES (%s,%s,%s,%s,%s,%s)", orders)
            cur.executemany("INSERT INTO refunds (order_id, amount_cents, status, created_on)"
                            " VALUES (%s,%s,%s,%s)", refunds)
            cur.executemany("INSERT INTO policy_sections (id, title, body) VALUES (%s,%s,%s)",
                            policies)
    with PostgresSaver.from_conn_string(url) as saver:
        saver.setup()
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("TRUNCATE checkpoints, checkpoint_blobs, checkpoint_writes")
    return {"customers": len(customers), "orders": len(orders), "refunds": len(refunds),
            "policy_sections": len(policies)}


def main() -> None:
    counts = seed(os.environ["DATABASE_URL"])
    print("seeded: " + ", ".join(f"{n} {name}" for name, n in counts.items()))


if __name__ == "__main__":
    main()
