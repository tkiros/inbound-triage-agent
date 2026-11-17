"""Database fixtures. Tests that touch Postgres use a separate `app_test` database on the
`make up` server, seeded once per session, so they never wipe your dev data."""

from __future__ import annotations

import os

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from data import seed


@pytest.fixture(scope="session")
def test_db():
    """Conninfo of a freshly seeded `app_test` database; DATABASE_URL points at it."""
    base = os.environ.get("DATABASE_URL")
    if not base:
        pytest.skip("needs DATABASE_URL: run `make up` and `make test`")
    try:
        with psycopg.connect(base, autocommit=True, connect_timeout=3) as admin:
            if not admin.execute("SELECT 1 FROM pg_database WHERE datname = 'app_test'").fetchone():
                admin.execute("CREATE DATABASE app_test")
    except psycopg.OperationalError as e:
        pytest.skip(f"database unreachable ({e}); run `make up`")
    url = make_conninfo(base, dbname="app_test")
    seed.seed(url)
    mp = pytest.MonkeyPatch()
    mp.setenv("DATABASE_URL", url)
    yield url
    mp.undo()


@pytest.fixture
def clean_db(test_db):
    """The test database with an empty pipeline (no emails, jobs, outbox, checkpoints)."""
    with psycopg.connect(test_db, autocommit=True) as conn:
        conn.execute("TRUNCATE outbox, approvals, jobs, emails CASCADE")
        conn.execute("DELETE FROM refunds WHERE email_id IS NOT NULL")
        conn.execute("TRUNCATE checkpoints, checkpoint_blobs, checkpoint_writes")
    return test_db


def rows(url: str, sql: str, params=()) -> list[tuple]:
    with psycopg.connect(url, autocommit=True) as conn:
        return conn.execute(sql, params).fetchall()


@pytest.fixture
def query(test_db):
    """query(sql, params) -> rows from the test database."""
    return lambda sql, params=(): rows(test_db, sql, params)
