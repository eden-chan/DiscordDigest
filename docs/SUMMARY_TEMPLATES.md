# Summary Templates & Strategy

This guide documents how summaries are produced and formatted across the digest tools. It covers the per-channel weekly summary flow and the compact weekly digest.

## Principles
- Prefer concise, skimmable bullets.
- Avoid overlong paragraphs; keep content under Discord post limits.
- Include links to source messages for quick follow-up.
- Neutral tone; summarize decisions, outcomes, and next steps.

## Inputs
- Indexed messages from SQLite (populated via `python -m digest --index-messages`).
- Optional Gemini API key (`GEMINI_API_KEY`) to improve quality; falls back to naive extraction otherwise.

## Per-Channel Weekly Template
Used by `python -m digest --post-weekly-per-channel` (rollup by default into a single digest message).

Structure:
1. Title: `**#<channel-name> — last 7d**`
2. Summary bullets (3–6) with key topics and decisions.
3. Optional top message links (up to `top_n`).

Example (per-channel blocks within one message):

```
**#engineering — last 7d**
- Landed auth refactor; sessions unified across services.
- Adopted feature flags for phased rollouts.
- RFC: queue backpressure; next steps posted.
 - https://discord.com/channels/<guild>/<channel>/<message>
 - https://discord.com/channels/<guild>/<channel>/<message>
```

Toggles:
- `--no-links` to omit the link list.
- `--min-messages` to skip low-activity channels.
- `--max-channels` and `--channels` to control scope.
- `--post-to digest|source` to choose the target.
- `--citations` to use inline-citation bullets with a numbered citations list.

## Compact Weekly Template (Whole Server)
Used by `python -m digest --post-weekly`.

Structure:
1. Title: `What's Happening — last 7d`
2. Top channels (count) and top users (count)
3. Highlights: short snippets with reactions/attachments meta and links.

## Summarization Strategy
1. Rank messages per channel using a weighted score:
   - Reactions, content length, links/attachments, and recency (see `digest/scoring.py`).
2. Select top `N` messages per channel (`TOP_N_CONVOS` or CLI override).
3. Summarize:
   - Deterministic: `build_inline_citation_summary(top)` composes bullets with `[n]` citations and a numbered link list.
   - If `GEMINI_API_KEY` is set, you can instead call Gemini with a compact system prompt.
   - Fallback: naive extraction of top message snippets with links.
4. Compose lines and post via `publish.post_text` with safe chunking (~1800 chars).

## Preview & Post Workflow
1. Index messages (recommended weekly window):
   - `python -m digest --index-messages --hours 168 --verbose`
2. Preview per-channel summaries (no posting):
   - `python -m digest --post-weekly-per-channel --hours 168 --dry-run`
3. Post summaries to the digest channel as ONE message (rollup):
   - `python -m digest --post-weekly-per-channel --hours 168 --post-to digest`
4. Post summaries to source channels (one per channel; heavier on write API):
   - `python -m digest --post-weekly-per-channel --hours 168 --post-to source`

## Configuration Tips
- `TIME_WINDOW_HOURS=168` for a weekly default window.
- `INCLUDE_CHANNEL_IDS` to narrow scope; or use `--channels` at runtime.
- Ensure `DISCORD_TOKEN_TYPE=Bot` and `GUILD_ID` are set for indexing/sync.
