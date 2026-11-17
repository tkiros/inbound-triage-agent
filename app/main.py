"""HTTP intake. POST /inbound stores the email and queues one job; the worker does the rest."""

from __future__ import annotations

import hmac
import os

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from app import jobs
from common import db

app = FastAPI(title="Inbound triage")


class InboundEmail(BaseModel):
    """What a mail provider's inbound webhook would post. `sender` is the verified envelope
    sender (in production: after SPF/DKIM checks); everything in subject and body is untrusted."""

    id: str = Field(min_length=1, max_length=200, description="the provider's message id")
    sender: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=320)
    subject: str = Field(max_length=1000)
    body: str = Field(max_length=50_000)


@app.get("/health")
def health() -> dict[str, str]:
    with db.connect() as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.post("/inbound", status_code=202)
def inbound(email: InboundEmail, x_inbound_token: str | None = Header(default=None)) -> dict:
    """Queue an email. Posting the same id again is accepted but creates no second job."""
    expected = os.environ.get("INBOUND_TOKEN")
    if expected and not hmac.compare_digest(x_inbound_token or "", expected):
        raise HTTPException(status_code=401, detail="bad or missing X-Inbound-Token")
    created = jobs.enqueue(email.model_dump())
    return {"id": email.id, "queued": created, "duplicate": not created}
