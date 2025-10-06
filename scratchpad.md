Scratchpad — Hikari Digest Bot
================================

Context7 Findings
-----------------
- Hikari REST usage: `hikari.RESTApp` with `async with rest_app.acquire(token)` to make REST calls (see README snippet). No first-class snippet for threads listing surfaced via Context7.
- Intents: Gateway usage requires configuring intents, but this project uses REST-only for crawling and posting, so intents aren’t directly needed for the initial batch job. Message Content rules still apply at the platform level.
- Reactions: Events exist for reaction deletion in docs; the REST message model should include a `reactions` summary when present (Discord API behavior). We’ll defensively handle absence.
- Threads: Context7 didn’t surface thread fetch snippets. Hunch: REST has helpers like `fetch_active_guild_threads(guild_id)` and `fetch_public_archived_threads(channel_id, before=...)`. We’ll prototype with guards.

Gotchas & Decisions
-------------------
- Message fetch window: Discord’s `GET channel messages` supports `limit` and `before/after` but not a server-side time predicate. We filter client-side and cap to `<=100` per channel for safety (digest/fetch.py:22).
- Links to messages: Use guild path when `guild_id` present, else `@me` (digest/fetch.py:45).
- Threads: Not implemented yet. Next step: enumerate active guild threads + archived threads per channel and reuse same fetch path. Context7 didn’t return a thread snippet; likely methods exist like `rest.fetch_active_guild_threads` and `rest.fetch_public_archived_threads(channel_id, before=...)`.
- Token types: Added support for `DISCORD_TOKEN_TYPE` (`Bot` default, `Bearer` supported). Provided OAuth token shows scope `messages.read` and expires weekly. Prefer Bot tokens for long-running crawls to avoid refresh flow. If using Bearer, we’ll need to add refresh handling.
- Guild provided: `Build Canada` (guild_id=1384033183112237208). Use `python -m digest --list-channels` to list all channels; no manual ID list is required.
 - Textual TUI: Keep UI minimal with `SelectionList` + `Log`. Use key bindings to avoid complex prompts. Background tasks via `asyncio.create_task` keep UI responsive.
- Rate limiting: Hikari REST handles it; we still limit concurrency with a small semaphore of 5 (digest/fetch.py:29).
- Summarization: Gemini Flash for speed; fallback to naive extract for environments without SDK (digest/summarize.py:24,42).
- Sizing: We keep outputs under ~1800 chars per message and split messages (digest/publish.py:6).
- Security: The digest scans all guild channels accessible to the token. Prefer Bot tokens for full coverage.

Open Tasks
----------
- Add thread crawling (active + archived public threads) and include their messages.
- Introduce a light conversation grouping (by thread or time-gapped sessions) and compute per-convo scores.
- Convert digest to embed formatting and optional per-digest thread creation.
- Optional slash command `/digest preview` to trigger from Discord.
- Add optional OAuth refresh support if Bearer tokens are used (`refresh_token` in env; new module to refresh before run).

Quick Validation Path
---------------------
- `python -m digest --list-channels` proves guild read scope.
- `python -m digest --dry-run --hours 24` fetches, scores, and prints top messages + summary without posting.
- After review, `python -m digest` posts to channel 1422772848681816144.
 - `python -m tui` opens an interactive read-only tester: select channels, hit `d` to see results live.
