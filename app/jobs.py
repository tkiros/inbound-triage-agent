"""A Postgres job queue: one job per email, claimed with FOR UPDATE SKIP LOCKED.

SKIP LOCKED makes each worker skip rows another worker has locked, so N workers can poll the
same table and no job is ever handed to two of them. No Redis or broker needed.
"""

from __future__ import annotations

from common import db

# One statement: find the oldest queued job nobody holds, lock it, mark it running.
CLAIM_SQL = """
UPDATE jobs SET status = 'running', attempts = attempts + 1, updated_at = now()
WHERE id = (
    SELECT id FROM jobs
    WHERE status = 'queued'
    ORDER BY id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING id, email_id
"""


def enqueue(email: dict) -> bool:
    """Store the email and queue a job for it. False if this email id was already received."""
    with db.connect() as conn, conn.transaction():
        new = conn.execute(
            "INSERT INTO emails (id, sender, subject, body) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (id) DO NOTHING RETURNING id",
            (email["id"], email["sender"], email["subject"], email["body"]),
        ).fetchone()
        if new:
            conn.execute("INSERT INTO jobs (email_id) VALUES (%s)", (email["id"],))
    return new is not None


def claim() -> tuple[int, str] | None:
    """(job id, email id) of the next job, now marked running; None when the queue is empty."""
    # ponytail: a worker that crashes mid-job leaves it 'running'. Add a lease (re-queue rows
    # whose updated_at is older than the longest possible run) when workers run unattended.
    with db.connect() as conn:
        return conn.execute(CLAIM_SQL).fetchone()


def finish(job_id: int, status: str, error: str | None = None) -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE jobs SET status = %s, last_error = %s, updated_at = now() WHERE id = %s",
            (status, error, job_id),
        )


def load_email(email_id: str) -> dict:
    with db.connect() as conn:
        row = conn.execute("SELECT id, sender, subject, body FROM emails WHERE id = %s",
                           (email_id,)).fetchone()
    return {"id": row[0], "sender": row[1], "subject": row[2], "body": row[3]}


def set_email_status(email_id: str, status: str) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE emails SET status = %s WHERE id = %s", (status, email_id))
