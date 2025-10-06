SHELL := /bin/bash
PY := .venv/bin/python
HOURS ?= 72

.PHONY: help setup prisma db-push install sync list-db list-live tui dry-run seed-json oauth-exchange oauth-refresh oauth-probe clean

help:
	@echo "Available targets:"
	@echo "  setup            - Create .venv, install deps (uv if available), prisma generate + db push"
	@echo "  prisma           - Run prisma generate + db push"
	@echo "  sync             - Upsert live channels to SQLite (requires Bot TOKEN and GUILD_ID)"
	@echo "  list-db          - List channels from SQLite"
	@echo "  list-live        - List channels live via REST (requires Bot TOKEN and GUILD_ID)"
	@echo "  tui              - Launch the Textual TUI"
	@echo "  dry-run          - Run digest dry-run (HOURS=$(HOURS))"
	@echo "  seed-json        - Upsert channels from data/channels.json into SQLite"
	@echo "  oauth-exchange   - Exchange OAUTH code and store to file+SQLite (CODE=...)"
	@echo "  oauth-refresh    - Refresh OAuth token and store to file+SQLite"
	@echo "  oauth-probe      - Show current token scope/identity"
	@echo "  clean            - Remove .venv and data/digest.db (local only)"

setup:
	bash scripts/setup.sh

prisma:
	$(PY) -m prisma generate && $(PY) -m prisma db push

install:
	bash scripts/setup.sh

sync:
	@if [ -z "$$TOKEN" ] || [ -z "$$GUILD_ID" ]; then \
		echo "Set TOKEN and GUILD_ID env vars: TOKEN=... GUILD_ID=... make sync"; exit 1; \
	fi
	DISCORD_TOKEN_TYPE=Bot TOKEN="$$TOKEN" GUILD_ID="$$GUILD_ID" $(PY) -m digest --sync-channels

list-db:
	$(PY) -m digest --list-db-channels

list-live:
	@if [ -z "$$TOKEN" ] || [ -z "$$GUILD_ID" ]; then \
		echo "Set TOKEN and GUILD_ID env vars: TOKEN=... GUILD_ID=... make list-live"; exit 1; \
	fi
	DISCORD_TOKEN_TYPE=Bot TOKEN="$$TOKEN" GUILD_ID="$$GUILD_ID" $(PY) -m digest --list-channels --live

tui:
	$(PY) -m tui

dry-run:
	$(PY) -m digest --dry-run --hours $(HOURS)

seed-json:
	$(PY) -m digest --seed-channels-from-json --json-path data/channels.json

oauth-exchange:
	@if [ -z "$$CODE" ]; then echo "Provide CODE=... make oauth-exchange"; exit 1; fi
	$(PY) -m digest --oauth-exchange --code "$$CODE" --out data/oauth_token.json

oauth-refresh:
	$(PY) -m digest --oauth-refresh --out data/oauth_token.json --oauth-refresh-update-channels-json

oauth-probe:
	$(PY) -m digest --oauth-probe

clean:
	rm -rf .venv data/digest.db
