.DEFAULT_GOAL := help
RUN := uv run --env-file .env
.PHONY: help up down reset db-check test lint data seed run worker replay pending approve \
        slack demo eval eval-test eval-offline

.env:
	cp .env.example .env

help:  ## list targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-13s %s\n", $$1, $$2}'

up: .env  ## start Postgres (localhost:5436) and wait until it's healthy
	docker compose up -d --wait db

down:  ## stop containers (data kept)
	docker compose down

reset:  ## DELETE the local database volume, then start fresh (dev data only)
	docker compose down -v
	$(MAKE) up

db-check:  ## confirm pg_search, vector and llm_calls exist
	@docker compose exec -T db psql -U app -d app -tAc \
	  "SELECT count(*) FROM pg_extension WHERE extname IN ('pg_search','vector')" | grep -qx 2 \
	  && docker compose exec -T db psql -U app -d app -tAc \
	  "SELECT to_regclass('llm_calls')" | grep -qx llm_calls \
	  && echo "db OK: pg_search, vector, llm_calls" || (echo "db check FAILED"; exit 1)

test: .env  ## unit + integration tests (DB tests use a separate app_test database)
	$(RUN) pytest -q

lint:  ## ruff
	uv run ruff check .

data:  ## regenerate the 300 synthetic emails (deterministic: same files every run)
	uv run python -m data.gen_emails

seed: .env  ## reset the database: schema, mock CRM, policy handbook, empty pipeline
	$(RUN) python -m data.seed

run: .env  ## intake API on http://localhost:8001 with auto-reload
	$(RUN) uvicorn app.main:app --reload --port 8001

worker: .env  ## process queued emails (offline mode when no ANTHROPIC_API_KEY)
	$(RUN) python -m app.worker

replay: .env  ## post all 300 emails to the running API (make run)
	$(RUN) python -m scripts.replay

pending: .env  ## list emails waiting for approval
	$(RUN) python -m app.approvals pending

approve: .env  ## approve/reject a held email: make approve ID=e012 DECISION=approve|reject
	@test -n "$(ID)" -a -n "$(DECISION)" || (echo "usage: make approve ID=e012 DECISION=approve|reject"; exit 1)
	$(RUN) python -m app.approvals decide $(ID) $(DECISION)

slack: .env  ## Slack approver (Socket Mode); needs the SLACK_* variables in .env
	$(RUN) python -m app.slack_approver

demo: .env  ## seed, replay 5 emails, auto-send one, approve one (works without a key)
	$(RUN) python -m scripts.demo

eval: .env  ## dev split: baseline vs pipeline vs offline -> evals/results-dev.md (calls the API)
	$(RUN) python -m evals.run --split dev

eval-test: .env  ## test split, all systems -- only once you've stopped tuning
	$(RUN) python -m evals.run --split test

eval-offline: .env  ## both splits, offline mode only (no model, free)
	$(RUN) python -m evals.run --split dev --systems offline
	$(RUN) python -m evals.run --split test --systems offline
