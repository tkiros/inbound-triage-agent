"""Intake and the jobs queue."""

import threading

from fastapi.testclient import TestClient

from app import jobs
from app.main import app


def email(i) -> dict:
    return {"id": f"q{i}", "sender": "a@example.com", "subject": "s", "body": "b"}


def test_posting_the_same_email_twice_creates_one_job(clean_db, query):
    client = TestClient(app)
    first = client.post("/inbound", json=email(1))
    second = client.post("/inbound", json=email(1))
    assert first.status_code == second.status_code == 202
    assert first.json()["queued"] is True
    assert second.json() == {"id": "q1", "queued": False, "duplicate": True}
    assert query("SELECT count(*) FROM jobs WHERE email_id = 'q1'")[0][0] == 1


def test_intake_validates_and_can_require_a_token(clean_db, monkeypatch):
    client = TestClient(app)
    assert client.post("/inbound", json={**email(2), "sender": "not-an-address"}).status_code == 422
    monkeypatch.setenv("INBOUND_TOKEN", "s3cret")
    assert client.post("/inbound", json=email(3)).status_code == 401
    ok = client.post("/inbound", json=email(3), headers={"X-Inbound-Token": "s3cret"})
    assert ok.status_code == 202


def test_concurrent_workers_never_claim_a_job_twice(clean_db):
    n_jobs, n_workers = 60, 8
    for i in range(n_jobs):
        jobs.enqueue(email(i))
    claimed: list[str] = []
    lock = threading.Lock()
    start = threading.Barrier(n_workers)

    def worker():
        start.wait()  # all workers hit the queue at the same moment
        while (job := jobs.claim()) is not None:
            with lock:
                claimed.append(job[1])

    threads = [threading.Thread(target=worker) for _ in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(claimed) == n_jobs
    assert len(set(claimed)) == n_jobs  # no job handed out twice
    assert jobs.claim() is None
