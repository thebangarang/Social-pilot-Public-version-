SHELL := /bin/bash
PY := .venv/bin/python
UV := uv
APP := $(notdir $(CURDIR))

.PHONY: help demo setup venv deps env db dev test admin freeze devnet-up devnet-down devnet-logs devnet-verify release-status

demo: setup
	@$(PY) scripts/demo_setup.py

help:
	@echo "make setup   - venv + deps + .env + DB schema"
	@echo "make venv    - create .venv (Python 3.14)"
	@echo "make deps    - install python deps into .venv"
	@echo "make env     - create .env from .env.example (random SECRET_KEY, 0600)"
	@echo "make db      - init SQLite schema + seed themes"
	@echo "make dev     - run uvicorn reload on 127.0.0.1:8100"
	@echo "make test    - run pytest"
	@echo "make admin   - create an admin user (interactive)"
	@echo "make demo    - setup + mode démo (aucune clé, aucun réseau)"
	@echo "make freeze  - pin deps into requirements.txt"
	@echo ""
	@echo "devnet ring (see docs/devnet.md, the internal release doc):"
	@echo "make devnet-up     - build (stamped) + start the devnet instance + verify"
	@echo "make devnet-verify - run the release.toml checks against devnet"
	@echo "make devnet-logs   - tail the devnet container"
	@echo "make devnet-down   - stop the devnet instance (state kept)"
	@echo "make release-status - drift: repo / devnet / production, all apps"

setup: venv deps env db
	@echo "[make] setup complete — run 'make admin' then 'make dev'"

venv:
	@test -d .venv || $(UV) venv .venv --python 3.14

deps: venv
	@$(UV) pip install --python .venv/bin/python -r requirements.txt

env:
	@test -f .env || { \
		key=$$(python3 -c "import secrets; print(secrets.token_hex(32))"); \
		sed "s/^SP_SECRET_KEY=.*/SP_SECRET_KEY=$$key/" .env.example > .env; \
		chmod 600 .env; \
		echo "[make] .env created (0600)"; \
	}

db:
	@$(PY) scripts/init_db.py

dev:
	@.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8100

test:
	@.venv/bin/pytest -q

admin:
	@.venv/bin/python scripts/create_user.py --role admin --name "Admin" --email admin@example.com

freeze:
	@$(UV) pip freeze --python .venv/bin/python | grep -v '^-e ' > requirements.txt
	@echo "[make] requirements.txt written"

# --- devnet ring (the staging instance on the build machine) -------------------
# Driven by release.py, configured by release.toml.
# Never promoted by these targets: promotion is a separate, confirmed gate.

devnet-up:
	@python3 release.py devnet up $(APP)

devnet-down:
	@python3 release.py devnet down $(APP)

devnet-logs:
	@python3 release.py devnet logs $(APP)

devnet-verify:
	@python3 release.py verify devnet $(APP)

release-status:
	@python3 release.py status
