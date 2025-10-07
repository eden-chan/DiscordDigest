SHELL := /bin/bash
PY := .venv/bin/python
HOURS ?= 72
CHANNELS ?=
VERBOSE ?=
POST_TO ?= digest
MIN_MESSAGES ?= 1
MAX_CHANNELS ?=
NO_LINKS ?=

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

# Per-channel options
PC_OPTS :=
ifneq ($(strip $(CHANNELS)),)
  PC_OPTS += --channels $(CHANNELS)
endif
ifneq ($(strip $(VERBOSE)),)
  PC_OPTS += --verbose
endif
ifneq ($(strip $(NO_LINKS)),)
  PC_OPTS += --no-links
endif
ifneq ($(strip $(MIN_MESSAGES)),)
  PC_OPTS += --min-messages $(MIN_MESSAGES)
endif
ifneq ($(strip $(MAX_CHANNELS)),)
  PC_OPTS += --max-channels $(MAX_CHANNELS)
endif
ifneq ($(strip $(POST_TO)),)
  PC_OPTS += --post-to $(POST_TO)
endif

.PHONY: help setup prisma db-push install sync list-db list-live tui dry-run seed-json clean per-channel-preview per-channel-digest per-channel-source digest-weekly-per-channel

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
	@echo "  state            - Show per-channel indexing checkpoints from SQLite"
	@echo "  oauth-login      - Start local OAuth login server and store token to SQLite"
	@echo "  oauth-exchange   - Exchange a code (CODE=...) and store token to SQLite"
	@echo "  oauth-refresh    - Refresh token from env/SQLite and store to SQLite"
	@echo "  oauth-probe      - Show current token scope/identity"
	@echo "  backfill         - Full backfill for selected channels (CHANNELS=.. [MAX=..] [SINCE=ISO] [VERBOSE=1])"
	@echo "  backfill-all     - Full backfill for all active channels (optional: MAX, SINCE, VERBOSE=1)"
	@echo "  backfill-progress- Tail the deterministic NDJSON progress log"
	@echo "  backfill-all-watch- Run backfill-all and live-tail progress log"
	@echo "  backfill-one       - Backfill a single channel (CHANNEL=...)"
	@echo "  backfill-one-watch - Backfill one channel with live-tail (CHANNEL=...)"
	@echo "  digest           - One-shot: sync -> index -> report (HOURS=$(HOURS))"
	@echo "  digest-weekly    - One-shot: sync -> index(last 7d) -> post compact summary"
	@echo "  per-channel-preview - Preview per-channel weekly summaries (HOURS=$(HOURS), CHANNELS=$(CHANNELS))"
	@echo "  per-channel-digest  - Post per-channel summaries to digest (POST_TO=digest)"
	@echo "  per-channel-source  - Post per-channel summaries to their source channels"
	@echo "  digest-weekly-per-channel - One-shot: sync -> index(7d) -> post per-channel summaries"
	@echo "  weekly-per-channel-preview - Preview (7d) per-channel summaries (no env needed)"
	@echo "  weekly-per-channel-digest  - Post (7d) per-channel summaries to digest"
	@echo "  weekly-per-channel-digest-thread - Post (7d) per-channel rollup in a new thread"
	@echo "  weekly-per-channel-source  - Post (7d) per-channel summaries to source channels"
	@echo "  weekly-per-channel-preview-citations - Preview (7d) per-channel with inline citations"
	@echo "  weekly-per-channel-digest-citations  - Post (7d) per-channel with inline citations to digest"
	@echo "  weekly-global-citations   - Post global highlights (Gemini bullets + citations)"
	@echo "  sync-threads     - Discover and upsert thread channels (optional: CHANNELS parent ids, VERBOSE=1)"
	@echo "  threads          - List discovered thread channels from SQLite"
	@echo "  threads-report   - Print a threads-only report (HOURS=$(HOURS))"
	@echo "  threads-archive-all - Sync archived threads across all parents (Bot token)"
	@echo "  threads-backfill-all - Backfill ALL messages for all thread channels (long)"
	@echo "  post-test        - Post a test message to the digest channel (TEXT=...)"
	@echo "  post-summary     - Post a summary of a single channel (CHANNEL=..., HOURS=$(HOURS))"
	@echo "  studio           - Open Prisma Studio (requires Node npx)"
	@echo "  gist-db          - Upload data/digest.db to a GitHub Gist (GITHUB_TOKEN required)"
	@echo "  hf-init          - Initialize local HF dataset git repo (.hf-dataset) (HF_REPO=acct/name)"
	@echo "  hf-push-db       - Copy data/digest.db into .hf-dataset and push to HF (HF_REPO=acct/name)"
	@echo "  kill-5555        - Kill any process listening on localhost:5555"
	@echo "  kill-port        - Kill any process listening on PORT (default 5555), usage: make kill-port PORT=3000"
	@echo "  skip-report      - List channels marked inactive (skipped due to access/type)"
	@echo "  clean            - Remove .venv and data/digest.db (local only)"

setup:
	bash scripts/setup.sh

prisma:
	PRISMA_PY_GENERATOR="$(PWD)/.venv/bin/prisma-client-py" .venv/bin/prisma generate && PRISMA_PY_GENERATOR="$(PWD)/.venv/bin/prisma-client-py" .venv/bin/prisma db push --skip-generate

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

state:
	$(PY) -m digest --show-state $(INDEX_OPTS)

backfill:
	@if [ -z "$(CHANNELS)" ]; then echo "Provide CHANNELS=comma-separated ids for backfill"; exit 1; fi
	$(PY) -m digest --index-messages --full --channels $(CHANNELS) $(if $(MAX),--max $(MAX),) $(if $(SINCE),--since $(SINCE),) $(INDEX_OPTS)

backfill-all:
	# Uses .env via python-dotenv (no shell export needed)
	$(PY) -m digest --sync-threads $(INDEX_OPTS)
	$(PY) -m digest --index-messages --full $(if $(MAX),--max $(MAX),) $(if $(SINCE),--since $(SINCE),) $(INDEX_OPTS)

backfill-one:
	@if [ -z "$(CHANNEL)" ]; then echo "Provide CHANNEL=<channel_id>"; exit 1; fi
	$(PY) -m digest --index-messages --full --channels $(CHANNEL) $(if $(MAX),--max $(MAX),) $(if $(SINCE),--since $(SINCE),) $(INDEX_OPTS)

backfill-progress:
	@echo "Tailing data/backfill_progress.log (Ctrl-C to stop)..."; \
	  mkdir -p data; \
	  touch data/backfill_progress.log; \
	  tail -n 200 -f data/backfill_progress.log

backfill-all-watch:
	@echo "Running backfill-all with progress at data/backfill_progress.log"; \
	  mkdir -p data; \
	  touch data/backfill_progress.log; \
	  $(PY) -m digest --sync-threads $(INDEX_OPTS); \
	  ( PROGRESS_LOG_PATH=data/backfill_progress.log $(PY) -m digest --index-messages --full $(if $(MAX),--max $(MAX),) $(if $(SINCE),--since $(SINCE),) $(INDEX_OPTS) ) & BF_PID=$$!; \
	  tail -n 200 -f data/backfill_progress.log & TAIL_PID=$$!; \
	  wait $$BF_PID || true; \
	  kill $$TAIL_PID >/dev/null 2>&1 || true

backfill-one-watch:
	@if [ -z "$(CHANNEL)" ]; then echo "Provide CHANNEL=<channel_id>"; exit 1; fi; \
	  echo "Running backfill-one ($(CHANNEL)) with progress at data/backfill_progress.log"; \
	  mkdir -p data; \
	  touch data/backfill_progress.log; \
	  ( PROGRESS_LOG_PATH=data/backfill_progress.log $(PY) -m digest --index-messages --full --channels $(CHANNEL) $(if $(MAX),--max $(MAX),) $(if $(SINCE),--since $(SINCE),) $(INDEX_OPTS) ) & BF_PID=$$!; \
	  tail -n 200 -f data/backfill_progress.log & TAIL_PID=$$!; \
	  wait $$BF_PID || true; \
	  kill $$TAIL_PID >/dev/null 2>&1 || true

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

# Per-channel helpers
per-channel-preview:
	$(PY) -m digest --post-weekly-per-channel --hours $(HOURS) --dry-run $(PC_OPTS)

per-channel-digest:
	$(PY) -m digest --post-weekly-per-channel --hours $(HOURS) --post-to digest $(PC_OPTS)

per-channel-source:
	$(PY) -m digest --post-weekly-per-channel --hours $(HOURS) --post-to source $(PC_OPTS)

digest-weekly-per-channel:
	# One-shot weekly: sync -> index(7d) -> post per-channel summaries (uses .env)
	$(PY) -m digest --sync-channels
	$(PY) -m digest --index-messages --hours 168 $(INDEX_OPTS)
	$(PY) -m digest --post-weekly-per-channel --hours 168 $(PC_OPTS)

# Simpler weekly aliases (no env overrides required)
weekly-per-channel-preview:
	$(PY) -m digest --post-weekly-per-channel --hours 168 --dry-run

weekly-per-channel-digest:
	$(PY) -m digest --post-weekly-per-channel --hours 168 --post-to digest

weekly-per-channel-digest-thread:
	$(PY) -m digest --post-weekly-per-channel --hours 168 --post-to digest --thread --summary-strategy citations

weekly-per-channel-source:
	$(PY) -m digest --post-weekly-per-channel --hours 168 --post-to source

weekly-per-channel-preview-citations:
	$(PY) -m digest --post-weekly-per-channel --hours 168 --dry-run --citations

weekly-per-channel-digest-citations:
	$(PY) -m digest --post-weekly-per-channel --hours 168 --post-to digest --citations

weekly-global-citations:
	$(PY) -m digest --post-weekly-global-citations --hours $(HOURS) $(if $(TOP_N),--top-n $(TOP_N),)

thread-test:
	$(PY) -m digest --post-thread-test

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

threads-archive-all:
	# Discover archived threads across all parents (requires Bot token)
	$(PY) -m digest --sync-threads-archive-all $(INDEX_OPTS)

threads-backfill-all:
	# Backfill ALL messages for all thread channels (can be very long)
	$(PY) -m digest --index-threads-full $(INDEX_OPTS)

post-test:
	$(PY) -m digest --post-test $(if $(TEXT),--text "$(TEXT)",)

post-summary:
	@if [ -z "$(CHANNEL)" ]; then echo "Provide CHANNEL=<channel_id>"; exit 1; fi
	$(PY) -m digest --post-summary-channel --channels $(CHANNEL) --hours $(HOURS)


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

hf-init:
	@if [ -z "$(HF_REPO)" ]; then echo "Set HF_REPO=account/dataset"; exit 1; fi
	@if ! command -v git >/dev/null 2>&1; then echo "git is required"; exit 1; fi
	@if ! git lfs version >/dev/null 2>&1; then echo "git-lfs is required. Install from https://git-lfs.com and run: git lfs install"; exit 1; fi
	@mkdir -p .hf-dataset && cd .hf-dataset && \
	  if [ ! -d .git ]; then git init && git lfs install && echo "*.db filter=lfs diff=lfs merge=lfs -text" > .gitattributes && echo "# HF dataset $(HF_REPO)" > README.md && git add . && git commit -m init; fi && \
	  ( git remote get-url origin >/dev/null 2>&1 || git remote add origin https://huggingface.co/datasets/$(HF_REPO) ) && \
	  git checkout -B $(if $(HF_BRANCH),$(HF_BRANCH),main)

hf-push-db:
	@if [ -z "$(HF_REPO)" ]; then echo "Set HF_REPO=account/dataset"; exit 1; fi
	@if [ ! -f prisma/data/digest.db ]; then echo "prisma/data/digest.db not found. Run: make index"; exit 1; fi
	HF_REPO=$(HF_REPO) HF_BRANCH=$(if $(HF_BRANCH),$(HF_BRANCH),main) DB_PATH=prisma/data/digest.db WORKDIR=.hf-dataset MSG="$(if $(MSG),$(MSG),update: upload SQLite)" bash scripts/hf_dataset_push.sh

PORT ?= 5555
kill-port:
	@bash scripts/kill_port.sh $(PORT)

kill-5555:
	@bash scripts/kill_port.sh 5555

clean:
	rm -rf .venv prisma/data/digest.db

skip-report:
	$(PY) -m digest --skip-report
oauth-login:
	$(PY) -m digest --oauth-login $(if $(NO_BROWSER),--no-browser,) $(if $(TIMEOUT),--timeout $(TIMEOUT),)

oauth-exchange:
	$(PY) -m digest --oauth-exchange $(if $(CODE),--code "$(CODE)",)

oauth-refresh:
	$(PY) -m digest --oauth-refresh

oauth-probe:
	$(PY) -m digest --oauth-probe
