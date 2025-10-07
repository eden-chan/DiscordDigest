SHELL := /bin/bash
PY := .venv/bin/python
HOURS ?= 72
CHANNELS ?=
VERBOSE ?=

# Optional CLI flags assembled from env vars
INDEX_OPTS :=
REPORT_OPTS :=
ifneq ($(strip $(CHANNELS)),)
  INDEX_OPTS += --channels $(CHANNELS)
endif
ifneq ($(strip $(VERBOSE)),)
  INDEX_OPTS += --verbose
  REPORT_OPTS += --verbose
endif

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
	@echo "  index            - Index messages to SQLite (HOURS=$(HOURS))"
	@echo "  report           - Print a channel/user activity report (HOURS=$(HOURS))"
	@echo "  backfill         - Full backfill for selected channels (CHANNELS=.. [MAX=..] [SINCE=ISO] [VERBOSE=1])"
	@echo "  backfill-all     - Full backfill for all active channels (optional: MAX, SINCE, VERBOSE=1)"
	@echo "  digest           - One-shot: sync -> index -> report (HOURS=$(HOURS))"
	@echo "  digest-weekly    - One-shot: sync -> index(last 7d) -> post compact summary"
	@echo "  sync-threads     - Discover and upsert thread channels (optional: CHANNELS parent ids, VERBOSE=1)"
	@echo "  threads          - List discovered thread channels from SQLite"
	@echo "  threads-report   - Print a threads-only report (HOURS=$(HOURS))"
	@echo "  post-test        - Post a test message to the digest channel (TEXT=...)"
	@echo "  post-summary     - Post a summary of a single channel (CHANNEL=..., HOURS=$(HOURS))"
	@echo "  seed-json        - Upsert channels from data/channels.json into SQLite"
	@echo "  oauth-exchange   - Exchange OAUTH code and store to file+SQLite (CODE=...)"
	@echo "  oauth-refresh    - Refresh OAuth token and store to file+SQLite"
	@echo "  oauth-probe      - Show current token scope/identity"
	@echo "  studio           - Open Prisma Studio (requires Node npx)"
	@echo "  db-shell         - Open SQLite shell for data/digest.db"
	@echo "  kill-5555        - Kill any process listening on localhost:5555"
	@echo "  kill-port        - Kill any process listening on PORT (default 5555), usage: make kill-port PORT=3000"
	@echo "  skip-report      - List channels marked inactive (skipped due to access/type)"
	@echo "  clean            - Remove .venv and data/digest.db (local only)"

setup:
	bash scripts/setup.sh

prisma:
	PRISMA_PY_GENERATOR="$(PWD)/.venv/bin/prisma-client-py" .venv/bin/prisma generate && PRISMA_PY_GENERATOR="$(PWD)/.venv/bin/prisma-client-py" .venv/bin/prisma db push

install:
	bash scripts/setup.sh

sync:
	# Uses .env via python-dotenv (no shell export needed)
	$(PY) -m digest --sync-channels

list-db:
	$(PY) -m digest --list-db-channels

list-live:
	# Uses .env via python-dotenv (no shell export needed)
	$(PY) -m digest --list-channels --live

tui:
	$(PY) -m tui

dry-run:
	$(PY) -m digest --dry-run --hours $(HOURS)

index:
	$(PY) -m digest --index-messages --hours $(HOURS) $(INDEX_OPTS)

report:
	$(PY) -m digest --report --hours $(HOURS) $(REPORT_OPTS)

backfill:
	@if [ -z "$(CHANNELS)" ]; then echo "Provide CHANNELS=comma-separated ids for backfill"; exit 1; fi
	$(PY) -m digest --index-messages --full --channels $(CHANNELS) $(if $(MAX),--max $(MAX),) $(if $(SINCE),--since $(SINCE),) $(INDEX_OPTS)

backfill-all:
	# Uses .env via python-dotenv (no shell export needed)
	$(PY) -m digest --index-messages --full $(if $(MAX),--max $(MAX),) $(if $(SINCE),--since $(SINCE),) $(INDEX_OPTS)

digest:
	# One-shot daily flow: sync -> index -> report (uses .env)
	$(PY) -m digest --sync-channels
	$(PY) -m digest --index-messages --hours $(HOURS) $(INDEX_OPTS)
	$(PY) -m digest --report --hours $(HOURS) $(REPORT_OPTS)

digest-weekly:
	# One-shot weekly: sync -> index(7d) -> post compact summary (uses .env)
	$(PY) -m digest --sync-channels
	$(PY) -m digest --index-messages --hours 168 $(INDEX_OPTS)
	$(PY) -m digest --post-weekly --hours 168

sync-threads:
	# Uses .env via python-dotenv (no shell export needed)
	$(PY) -m digest --sync-threads $(INDEX_OPTS)

# Daily digest flow: sync -> index(24h) -> post compact summary
digest-daily:
	$(PY) -m digest --sync-channels
	$(PY) -m digest --index-messages --hours 24 $(INDEX_OPTS)
	$(PY) -m digest --post-weekly --hours 24

threads:
	$(PY) -m digest --list-threads

threads-report:
	$(PY) -m digest --threads-report --hours $(HOURS) $(REPORT_OPTS)

post-test:
	$(PY) -m digest --post-test $(if $(TEXT),--text "$(TEXT)",)

post-summary:
	@if [ -z "$(CHANNEL)" ]; then echo "Provide CHANNEL=<channel_id>"; exit 1; fi
	$(PY) -m digest --post-summary-channel --channels $(CHANNEL) --hours $(HOURS)

seed-json:
	$(PY) -m digest --seed-channels-from-json --json-path data/channels.json

oauth-exchange:
	@if [ -z "$$CODE" ]; then echo "Provide CODE=... make oauth-exchange"; exit 1; fi
	$(PY) -m digest --oauth-exchange --code "$$CODE" --out data/oauth_token.json

oauth-refresh:
	$(PY) -m digest --oauth-refresh --out data/oauth_token.json --oauth-refresh-update-channels-json

oauth-probe:
	$(PY) -m digest --oauth-probe

studio:
	@$(MAKE) -s kill-port PORT=$(PORT) >/dev/null 2>&1 || true
	@if command -v npx >/dev/null 2>&1; then \
		echo "Launching Prisma Studio via npx on port $(PORT)..."; \
		PRISMA_PY_GENERATOR=prisma-client-py npx prisma studio --schema prisma/schema.prisma --port $(PORT); \
	elif [ -x ".venv/bin/prisma" ]; then \
		echo "Python Prisma CLI typically doesn't support studio. Install Node and try: npx prisma studio --port $(PORT)"; exit 1; \
	else \
		echo "No Prisma Studio available. Install Node and run: npx prisma studio --port $(PORT)"; exit 1; \
	fi

db-shell:
	@if command -v sqlite3 >/dev/null 2>&1; then \
		sqlite3 data/digest.db; \
	else \
		echo "sqlite3 CLI not found. You can use Python: python -c 'import sqlite3;import sys;[print(r) for r in sqlite3.connect(\"data/digest.db\").execute(\".tables\")]'"; \
	fi

PORT ?= 5555
kill-port:
	@bash scripts/kill_port.sh $(PORT)

kill-5555:
	@bash scripts/kill_port.sh 5555

clean:
	rm -rf .venv data/digest.db

skip-report:
	$(PY) -m digest --skip-report
