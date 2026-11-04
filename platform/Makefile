# Developer & operations entry points for the messaging platform.
PYTHON ?= python3
VENV   ?= ../../.venv
PIP    = $(VENV)/bin/pip
PY     = $(VENV)/bin/python

.PHONY: help venv test test-security load lint openapi run migrate seed superuser \
        up down logs backup restore deploy android-check android-probe android-login-probe

help:
	@echo "make venv        install backend dependencies into ../../.venv"
	@echo "make test        run the full backend test suite (real PostgreSQL)"
	@echo "make load        quick load test against a running API"
	@echo "make run         start the API locally on :8000"
	@echo "make migrate     apply database migrations"
	@echo "make seed        create system roles and default settings"
	@echo "make superuser   bootstrap a super admin (USER=.. PASS=..)"
	@echo "make openapi     export the OpenAPI document to docs/openapi.json"
	@echo "make up|down     start/stop the docker compose stack"
	@echo "make backup      run an on-demand backup inside the stack"
	@echo "make android-check  static check of the Android platform client"
	@echo "make android-probe  replay the Android client contract against :8000"
	@echo "make android-login-probe  replay the LoginActivity -> backend auth flows"

venv:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install -r backend/requirements-dev.txt

test:
	cd backend && ../../../.venv/bin/python -m pytest -q

load:
	$(PY) tests/load/bench.py --messages 300 --concurrency 20

run:
	cd backend && ../../../.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

migrate:
	cd backend && ../../../.venv/bin/alembic upgrade head

seed:
	cd backend && ../../../.venv/bin/python -m app.cli seed

superuser:
	cd backend && ../../../.venv/bin/python -m app.cli create-superuser \
		--username $(USER) --password '$(PASS)'

openapi:
	$(PY) scripts/export_openapi.py

up:
	cd deploy && docker compose up -d --build

down:
	cd deploy && docker compose down

logs:
	cd deploy && docker compose logs -f --tail=200 api

backup:
	cd deploy && docker compose exec -T backup /scripts/backup.sh

restore:
	cd deploy && docker compose exec -T backup /scripts/restore.sh $(FILE)

deploy:
	cd deploy && ./scripts/deploy.sh

android-check:
	$(PY) tools/check_android_phase1.py

android-probe:
	$(PY) tools/android_contract_probe.py --server $${SERVER:-http://127.0.0.1:8000}

android-login-probe:
	$(PY) tools/android_login_probe.py --server $${SERVER:-http://127.0.0.1:8000}
