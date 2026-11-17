-- Triage tables. Runs once on a fresh volume (after 20_app_schema.sql); `make seed` also runs
-- every file in db/init, so all statements here must be safe to run twice.
-- Money is stored in integer cents: no float rounding on refunds.

-- Mock CRM ---------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS customers (
    id     int  PRIMARY KEY,
    email  text NOT NULL UNIQUE,
    name   text NOT NULL,
    tier   text NOT NULL CHECK (tier IN ('standard', 'plus', 'vip'))
);

CREATE TABLE IF NOT EXISTS orders (
    id           text PRIMARY KEY,                    -- e.g. LS-482913
    customer_id  int  NOT NULL REFERENCES customers (id),
    status       text NOT NULL CHECK (status IN
                     ('processing', 'shipped', 'delivered', 'cancelled', 'refunded')),
    amount_cents int  NOT NULL,
    item         text NOT NULL,
    ordered_on   date NOT NULL
);
CREATE INDEX IF NOT EXISTS orders_customer ON orders (customer_id);

CREATE TABLE IF NOT EXISTS refunds (
    id           serial PRIMARY KEY,
    order_id     text NOT NULL REFERENCES orders (id),
    amount_cents int  NOT NULL,
    status       text NOT NULL,
    created_on   date NOT NULL DEFAULT current_date,
    email_id     text UNIQUE      -- set when this pipeline issued it: one refund per email, ever
);

-- Policy handbook, searched with BM25 (pg_search). The English stemmer lets "refund" match
-- "Refunds"; the `|||` operator in app/policies.py tokenizes the query text instead of parsing it.
CREATE TABLE IF NOT EXISTS policy_sections (
    id    text PRIMARY KEY,                           -- e.g. §2.3
    title text NOT NULL,
    body  text NOT NULL
);
CREATE INDEX IF NOT EXISTS policy_sections_bm25 ON policy_sections USING bm25 (
    id,
    (title::pdb.simple('stemmer=english', 'stopwords_language=english')),
    (body::pdb.simple('stemmer=english', 'stopwords_language=english'))
);

-- Pipeline ---------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS emails (
    id              text PRIMARY KEY,                 -- the mail provider's message id
    sender          text NOT NULL,                    -- trusted envelope sender
    subject         text NOT NULL,
    body            text NOT NULL,
    received_at     timestamptz NOT NULL DEFAULT now(),
    status          text NOT NULL DEFAULT 'queued' CHECK (status IN
                        ('queued', 'awaiting_approval', 'sent', 'rejected', 'needs_human')),
    intent          text,
    hold_reasons    text[],
    draft           text,
    proposed_action jsonb
);

-- The queue: one job per email (UNIQUE), claimed with FOR UPDATE SKIP LOCKED in app/jobs.py.
CREATE TABLE IF NOT EXISTS jobs (
    id         bigserial PRIMARY KEY,
    email_id   text NOT NULL UNIQUE REFERENCES emails (id),
    status     text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
    attempts   int  NOT NULL DEFAULT 0,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS jobs_queued ON jobs (id) WHERE status = 'queued';

-- First decision wins: a second click finds the row already there.
CREATE TABLE IF NOT EXISTS approvals (
    email_id   text PRIMARY KEY REFERENCES emails (id),
    decision   text NOT NULL CHECK (decision IN ('approve', 'reject')),
    decided_by text NOT NULL,
    decided_at timestamptz NOT NULL DEFAULT now()
);

-- "Sending" = a row here. Keyed by email id, so a reply can be written once and only once.
CREATE TABLE IF NOT EXISTS outbox (
    email_id   text PRIMARY KEY REFERENCES emails (id),
    to_address text NOT NULL,
    subject    text NOT NULL,
    body       text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
