# Architecture & API Boundaries

This project separates external I/O (Discord API) from reporting and summarization logic.

Core rules:
- Indexing/Syncing is the only place we call the Discord API to read messages/channels.
- Reporting, summaries, and TUI all read from SQLite via Prisma models.
- Posting to Discord is performed via `publish.post_text` (REST write), decoupled from data reads.

## Components

- `digest/indexer.py` — Fetches messages from Discord and upserts into SQLite. Supports incremental and full backfill. Handles rate limits and logs deterministic NDJSON progress.
- `digest/db.py` — Prisma client helpers and upsert/list operations.
- `digest/report.py` — Pure DB readers and formatters:
  - `build_activity_snapshot()` deterministic channel/user counts + highlights
  - `build_inline_citation_summary()` deterministic citations bullets
  - `print_report()`, `build_compact_summary()`, and `post_compact_summary()` use only DB.
- `digest/per_channel.py` — Per-channel summary builder/preview/post using only DB messages.
- `digest/publish.py` — Posting helper that chunks text safely to Discord.
- `digest/main.py` — CLI wiring:
  - `--sync-*` and `--index-*` commands do reads from Discord to populate SQLite.
  - `--report`, `--post-weekly`, `--post-weekly-per-channel`, `--threads-report`, etc. read only from SQLite.
  - `run_preview()` now reads solely from SQLite.

## Data Flow
1. `--sync-channels` / `--sync-threads*` discover channels/threads via API and upsert channels.
2. `--index-messages` (incremental) or `--index-messages --full` (backfill) fetch messages via API and upsert into SQLite.
3. Reports and summaries read from SQLite only and post via REST.

Schema sync
- Run `make setup` or `make prisma` to generate the Prisma client and push the schema to SQLite.
- Regular commands do NOT run `prisma db push` automatically. To force it, set `DIGEST_AUTO_DB_PUSH=1` in the environment (not recommended for normal use).

## Consequences
- Running reporting commands without prior indexing will yield empty windows and guide you to run `--index-messages`.
- The citation-style summaries are deterministic and reference only messages persisted in the DB.
