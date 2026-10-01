# Thin wrappers over the commands in docs/GETTING_STARTED.md. Every target is
# one or two commands you can also run by hand (Windows: see that document).
#
#   make setup          venv + Python deps + npm deps + Playwright browser
#   make dev-stack      embedded PostgreSQL + mock data + API on :8000
#   make dev-frontend   Vite dev server on :5173, talking to the API
#   make dev-fixtures   Vite dev server on fixtures, no backend needed
#   make test           every test that needs no running server
#   make test-e2e-live  Playwright against a running dev stack

VENV    ?= .venv
PY      := $(abspath $(VENV))/bin/python
NPM     := npm --prefix frontend

.PHONY: setup dev-stack dev-stack-stop dev-frontend dev-fixtures \
        test test-backend test-db test-frontend test-e2e test-e2e-live lint

setup:
	python3.11 -m venv $(VENV)
	$(PY) -m pip install -r backend/requirements-dev.txt -r backend/requirements-worker.txt
	$(NPM) ci
	cd frontend && npx playwright install chromium

dev-stack:
	cd backend && $(PY) scripts/dev_stack.py

dev-stack-stop:
	cd backend && $(PY) scripts/dev_stack.py --stop

dev-frontend:
	$(NPM) run dev

dev-fixtures:
	cd frontend && VITE_FIXTURES=1 npm run dev

# Unit tests: no database, no server.
test-backend:
	cd backend && $(PY) -m pytest -q --ignore=tests/e2e

# SQL and API tests against an embedded PostgreSQL (pgserver), or
# E2E_DATABASE_URL if set (its database name must contain "test").
test-db:
	cd backend && $(PY) -m pytest -q tests/e2e tests/test_metric_functions_sql.py

test-frontend:
	$(NPM) run test
	$(NPM) run build
	$(NPM) run check:i18n

# Playwright on fixtures: starts its own dev server.
test-e2e:
	cd frontend && npx playwright test --project=fixtures

# Playwright against `make dev-stack` (must already be running).
# The live specs (frontend/e2e/live/) are not written yet - see
# docs/status/REMAINING_WORK.md.
test-e2e-live:
	cd frontend && npx playwright test --project=live

test: test-backend test-db test-frontend test-e2e

lint:
	cd backend && $(PY) -m ruff check .
	$(NPM) run lint
