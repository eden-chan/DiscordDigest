import asyncio
import datetime as dt
import argparse
import os
import json

from dotenv import load_dotenv
from .config import Config
from .fetch import SimpleMessage
from .scoring import select_top
from .publish import post_text
from .summarize import naive_extract, summarize_with_gemini
from .oauth import (
        exchange_from_env,
        refresh_from_env,
        exchange_code,
        authorize_and_exchange,
        build_authorize_url,
        probe_token,
    )
from .discover import list_guild_channels


async def run_preview(dry_run: bool = False, hours: int | None = None) -> None:
    """Preview or post a cross-channel summary strictly from SQLite.

    No live Discord API calls; requires a prior `--index-messages` run.
    """
    from .db import ensure_schema, connect_client

    cfg = Config.from_env()
    lookback = hours if hours is not None else cfg.time_window_hours
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=lookback)

    await ensure_schema()
    client = await connect_client()
    try:
        rows = await client.message.find_many(
            where={"createdAt": {"gte": since}},
            order={"createdAt": "desc"},
        )
    finally:
        await client.disconnect()

    now = dt.datetime.now(dt.timezone.utc)
    if not rows:
        msg = f"No recent messages in SQLite (last {lookback}h). Run: python -m digest --index-messages --hours {lookback}"
        if dry_run:
            print(msg)
        else:
            await post_text(cfg.token, cfg.digest_channel_id, [msg], token_type=cfg.token_type)
        return

    msgs: list[SimpleMessage] = []
    for m in rows:
        msgs.append(
            SimpleMessage(
                id=int(m.id),
                channel_id=int(m.channelId),
                author_id=int(m.authorId),
                created_at=m.createdAt,
                content=m.content or "",
                link=m.link or "",
                reactions_total=int(m.reactionsTotal) if m.reactionsTotal is not None else 0,
                attachments=int(m.attachmentsCount) if m.attachmentsCount is not None else 0,
            )
        )

    top_messages = select_top(msgs, cfg.top_n, now=now, window_start=since)
    if cfg.gemini_api_key:
        summary = await summarize_with_gemini(cfg.gemini_api_key, top_messages)
        if not summary or summary.strip() in {"(No summary returned.)"} or summary.lower().startswith("gemini error"):
            summary = naive_extract(top_messages)
    else:
        summary = naive_extract(top_messages)

    title = f"What's Happening (last {lookback}h)"
    if dry_run:
        print(title)
        print("== Top messages ==")
        for m in top_messages:
            ts = m.created_at.strftime("%Y-%m-%d %H:%M:%S %Z")
            preview = (m.content or "").replace("\n", " ")[:140]
            print(f"- {ts} | ch={m.channel_id} reacts={m.reactions_total} att={m.attachments}")
            print(f"  {preview}")
            print(f"  {m.link}")
        print("== Summary ==")
        print(summary)
    else:
        lines = [f"**{title}**", summary]
        await post_text(cfg.token, cfg.digest_channel_id, lines, token_type=cfg.token_type)


async def run_list_channels(live: bool = False) -> None:
    # List channels from SQLite (source of truth). Optionally include live REST listing.
    try:
        from .db import connect_client, list_db_channels, ensure_schema
        await ensure_schema()
        client = await connect_client()
        try:
            cfg = Config.from_env()
            rows = await list_db_channels(client, cfg.guild_id)
            if rows:
                print("Channels in DB:")
                for ch in rows:
                    label = f"#{ch.name} — {ch.id}" if ch.name else f"Channel {ch.id}"
                    print(f"- {label} [{ch.type}]")
                if not live:
                    return
        finally:
            await client.disconnect()
    except Exception as e:
        pass
    if not live:
        print("No channels in SQLite. Run `python -m digest --sync-channels` (Bot token) to populate.")
        return

    # Live listing via REST requires a Bot token
    cfg = Config.from_env()
    if cfg.token_type.lower() != "bot" or not cfg.guild_id:
        print("--live requires DISCORD_TOKEN_TYPE=Bot and GUILD_ID set.")
        return
    items = await list_guild_channels(cfg.token, cfg.token_type, cfg.guild_id)
    print("Guild channels (live):")
    for cid, name, ctype in items:
        print(f"- {name} [{ctype}] — {cid}")


def main() -> None:
    # Ensure .env is loaded for all subcommands (including OAuth helpers)
    try:
        load_dotenv()
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Digest utility")
    parser.add_argument("--list-channels", action="store_true", help="List channels from SQLite; add --live for REST")
    parser.add_argument("--live", action="store_true", help="With --list-channels, fetch live via REST (Bot token)")
    parser.add_argument("--sync-channels", action="store_true", help="Upsert live channels into SQLite (Bot token)")
    parser.add_argument("--list-db-channels", action="store_true", help="List channels stored in SQLite")
    parser.add_argument("--guild", type=int, help="Override guild id for DB/list operations")
    # JSON seeding removed; use --sync-channels instead.
    parser.add_argument("--index-messages", action="store_true", help="Index recent messages from active channels into SQLite")
    parser.add_argument("--channels", help="Comma-separated channel IDs for indexing (optional)")
    parser.add_argument("--verbose", action="store_true", help="Verbose output for indexing/reporting")
    parser.add_argument("--report", action="store_true", help="Print a quick report from SQLite for the time window")
    parser.add_argument("--full", action="store_true", help="Full backfill mode (requires --channels)")
    parser.add_argument("--max", type=int, help="Max messages per channel in full mode")
    parser.add_argument("--since", help="ISO timestamp cutoff for full mode (e.g., 2024-01-01T00:00:00Z)")
    parser.add_argument("--post-weekly", action="store_true", help="Post a compact weekly summary to the configured digest channel")
    parser.add_argument("--post-weekly-global-citations", action="store_true", help="Post a global weekly highlights summary (Gemini bullets + citations)")
    parser.add_argument("--post-weekly-per-channel", action="store_true", help="Post weekly summaries per channel (rollup by default)")
    parser.add_argument("--skip-report", action="store_true", help="List channels marked inactive (e.g., due to 403)")
    parser.add_argument("--sync-threads", action="store_true", help="Discover and upsert thread channels for the guild")
    parser.add_argument("--sync-threads-archive-all", action="store_true", help="Discover and upsert archived thread channels for ALL parent channels in the guild (Bot token)")
    parser.add_argument("--list-threads", action="store_true", help="List discovered thread channels from SQLite")
    parser.add_argument("--threads-report", action="store_true", help="Print a threads-only report")
    parser.add_argument("--index-threads-full", action="store_true", help="Backfill ALL messages for all thread channels (can be very long)")
    parser.add_argument("--post-test", action="store_true", help="Post a test message to the digest channel")
    parser.add_argument("--post-thread-test", action="store_true", help="Create a test thread in the digest channel and post a test message (Bot token)")
    parser.add_argument("--text", help="Text for --post-test")
    parser.add_argument("--post-summary-channel", action="store_true", help="Post a summary of a single channel to the digest channel")
    parser.add_argument("--dry-run", action="store_true", help="Print digest to stdout without posting")
    parser.add_argument("--hours", type=int, help="Override lookback window in hours")
    parser.add_argument("--post-to", choices=["digest", "source"], default="digest", help="Target for per-channel posts (digest/source)")
    parser.add_argument("--summary-strategy", choices=["citations", "gemini", "naive", "gemini_citations"], help="Summary style for per-channel outputs")
    parser.add_argument("--min-messages", type=int, default=1, help="Minimum messages per channel to include")
    parser.add_argument("--top-n", type=int, default=5, help="Top messages per channel to cite/summarize")
    parser.add_argument("--max-channels", type=int, help="Maximum number of channels to include")
    parser.add_argument("--no-links", action="store_true", help="Omit message link list in per-channel summary")
    parser.add_argument("--citations", action="store_true", help="Use inline-citation summary bullets for per-channel output")
    parser.add_argument("--only-text", action="store_true", help="Restrict indexing to GUILD_TEXT channels only (exclude threads/news)")
    parser.add_argument("--show-state", action="store_true", help="Show per-channel indexing checkpoints from SQLite")
    parser.add_argument("--thread", action="store_true", help="Create a thread in the digest channel and post the rollup there (Bot token required)")
    parser.add_argument("--thread-name", help="Custom thread name for --thread (default: 'Weekly Digest — <days>d')")
    parser.add_argument("--oauth-exchange", action="store_true", help="Exchange OAUTH_CODE for tokens using env vars")
    parser.add_argument("--code", help="Authorization code for --oauth-exchange (overrides OAUTH_CODE env)")
    parser.add_argument("--oauth-refresh", action="store_true", help="Refresh access token using env vars (env or SQLite)")
    parser.add_argument("--out", help="Write resulting JSON to a file (optional)")
    parser.add_argument("--oauth-login", action="store_true", help="Start a local server to capture code and exchange automatically")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser automatically for --oauth-login")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout in seconds for OAuth login code capture")
    parser.add_argument("--oauth-probe", action="store_true", help="Probe current token and print scopes/identity")
    args = parser.parse_args()

    if args.oauth_probe or args.oauth_login or args.oauth_exchange or args.oauth_refresh:
        if args.oauth_probe:
            cfg = Config.from_env()
            result = asyncio.run(probe_token(cfg.token, cfg.token_type))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        if args.oauth_login:
            cid = os.getenv("OAUTH_CLIENT_ID")
            secret = os.getenv("OAUTH_CLIENT_SECRET")
            redirect = os.getenv("OAUTH_REDIRECT_URI")
            scope = os.getenv("OAUTH_SCOPE", "messages.read")
            if not all([cid, secret, redirect]):
                raise SystemExit(
                    "Missing OAUTH_CLIENT_ID/OAUTH_CLIENT_SECRET/OAUTH_REDIRECT_URI in environment"
                )
            result = asyncio.run(
                authorize_and_exchange(
                    cid,
                    secret,
                    redirect,
                    scope=scope,
                    open_browser=not args.no_browser,
                    timeout=args.timeout,
                )
            )
        elif args.oauth_exchange:
            # Try env-driven exchange; if OAUTH_CODE is missing, prompt interactively.
            cid = os.getenv("OAUTH_CLIENT_ID")
            secret = os.getenv("OAUTH_CLIENT_SECRET")
            redirect = os.getenv("OAUTH_REDIRECT_URI")
            code = args.code or os.getenv("OAUTH_CODE")
            if not all([cid, secret, redirect]):
                raise SystemExit(
                    "Missing OAUTH_CLIENT_ID/OAUTH_CLIENT_SECRET/OAUTH_REDIRECT_URI in environment"
                )
            if not code:
                # Provide a helper authorize URL for convenience
                try:
                    import urllib.parse as _up

                    auth_url = (
                        "https://discord.com/oauth2/authorize?" +
                        _up.urlencode(
                            {
                                "client_id": cid,
                                "response_type": "code",
                                "redirect_uri": redirect,
                                # Use messages.read; adding bot scope is optional and not needed for this exchange
                                "scope": os.getenv("OAUTH_SCOPE", "messages.read"),
                                "prompt": "consent",
                            }
                        )
                    )
                    print("Open this URL to authorize and get a new code:")
                    print(auth_url)
                except Exception:
                    pass
                try:
                    code = input("Paste authorization code (code=…): ").strip()
                except KeyboardInterrupt:
                    raise SystemExit(1)
                if not code:
                    raise SystemExit("No code provided")
            result = asyncio.run(exchange_code(cid, secret, code, redirect))
        else:
            result = asyncio.run(refresh_from_env())
        # Add created_at timestamp for caching convenience
        try:
            import datetime as _dt

            result = dict(result)
            if "created_at" not in result:
                result["created_at"] = int(_dt.datetime.now(_dt.timezone.utc).timestamp())
        except Exception:
            pass
        # Always print the resulting JSON, even if writing to a file
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"Wrote {args.out}")
        # Also persist to SQLite for central auth storage
        try:
            from .db import upsert_oauth_token_sync

            token_type = str(result.get("token_type", "Bearer"))
            access = str(result.get("access_token")) if result.get("access_token") else None
            refresh = result.get("refresh_token")
            scope = result.get("scope")
            expires_at = None
            exp = result.get("expires_in")
            if isinstance(exp, (int, float)):
                expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=int(exp))
            if access:
                upsert_oauth_token_sync(
                    provider="discord",
                    token_type=token_type,
                    access_token=access,
                    refresh_token=refresh,
                    scope=scope,
                    expires_at=expires_at,
                )
                print("Stored OAuth token in SQLite (provider=discord).")
        except Exception as e:
            print(f"Warning: failed to store OAuth token in SQLite: {e}")
        return

    if args.list_channels:
        asyncio.run(run_list_channels(live=args.live))
        return

    if args.show_state:
        async def _do_state() -> None:
            from .db import ensure_schema, connect_client
            await ensure_schema()
            client = await connect_client()
            try:
                cfg = Config.from_env()
                # Determine set of channels to show
                chan_ids = None
                if args.channels:
                    try:
                        chan_ids = [int(x.strip()) for x in args.channels.split(",") if x.strip()]
                    except Exception:
                        chan_ids = None
                where = {}
                if chan_ids:
                    where["id"] = {"in": chan_ids}
                elif cfg.guild_id:
                    where["guildId"] = int(cfg.guild_id)
                rows = await client.channel.find_many(where=where or None)
                if not rows:
                    print("No channels found in SQLite.")
                    return
                print("Channel indexing checkpoints:")
                for ch in sorted(rows, key=lambda r: int(r.id)):
                    st = await client.channelstate.find_unique(where={"channelId": int(ch.id)})
                    # Oldest/newest in DB for this channel
                    oldest = await client.message.find_first(where={"channelId": int(ch.id)}, order={"createdAt": "asc"})
                    newest = await client.message.find_first(where={"channelId": int(ch.id)}, order={"createdAt": "desc"})
                    print(
                        f"- {ch.name or ch.id} [{ch.type}] — {int(ch.id)}\n"
                        f"    lastIndexedAt={getattr(st,'lastIndexedAt',None)} lastMsgAt={getattr(st,'lastMessageCreatedAt',None)}\n"
                        f"    backfillBeforeId={getattr(st,'backfillBeforeId',None)} backfillOldestAt={getattr(st,'backfillOldestAt',None)}\n"
                        f"    db_range=({getattr(oldest,'createdAt',None)} .. {getattr(newest,'createdAt',None)})"
                    )
            finally:
                await client.disconnect()
        asyncio.run(_do_state())
        return

    if args.sync_channels:
        # Live fetch required
        cfg = Config.from_env()
        if cfg.token_type.lower() != "bot":
            print("--sync-channels requires DISCORD_TOKEN_TYPE=Bot")
            return
        gid = args.guild or cfg.guild_id
        if not gid:
            print("GUILD_ID is required for --sync-channels")
            return
        from .db import connect_client, upsert_guild, upsert_channels, ensure_schema

        async def _do_sync() -> None:
            await ensure_schema()
            items = await list_guild_channels(cfg.token, cfg.token_type, gid)
            client = await connect_client()
            try:
                await upsert_guild(client, gid)
                norm = [(cid, name, ctype, None, None, None, None, 1) for cid, name, ctype in items]
                await upsert_channels(client, gid, norm)
                print(f"Synced {len(items)} channels to SQLite.")
            finally:
                await client.disconnect()

        asyncio.run(_do_sync())
        return

    if args.list_db_channels:
        from .db import connect_client, list_db_channels, ensure_schema

        async def _do_list() -> None:
            await ensure_schema()
            client = await connect_client()
            try:
                cfg = Config.from_env()
                gid = args.guild or cfg.guild_id
                rows = await list_db_channels(client, gid)
                print("Channels in DB:")
                for ch in rows:
                    print(f"- {ch.name or ch.id} [{ch.type}] — {ch.id}")
            finally:
                await client.disconnect()

        asyncio.run(_do_list())
        return

    # JSON seeding path removed.

    if args.index_messages:
        async def _do_index() -> None:
            from .indexer import index_messages
            chans = None
            if args.channels:
                try:
                    chans = [int(x.strip()) for x in args.channels.split(",") if x.strip()]
                except Exception:
                    chans = None
            # Parse since cutoff if provided
            since_dt = None
            if args.since:
                try:
                    import datetime as _dt
                    s = args.since.replace("Z", "+00:00")
                    since_dt = _dt.datetime.fromisoformat(s)
                except Exception:
                    since_dt = None
            # Restrict to text-only if requested
            types = {"GUILD_TEXT"} if args.only_text else None
            per = await index_messages(
                hours=args.hours,
                channel_ids=chans,
                verbose=args.verbose,
                full=args.full,
                max_total=args.max,
                since_dt=since_dt,
                allowed_types=types,
            )
            total = sum(per.values())
            if args.verbose:
                print("[index] Per-channel counts:")
                for cid, cnt in per.items():
                    print(f"- {cid}: {cnt}")
            print(f"Indexed {total} messages into SQLite.")
        asyncio.run(_do_index())
        return

    if args.report:
        async def _do_report() -> None:
            from .report import print_report
            await print_report(hours=args.hours or 72, verbose=args.verbose)
        asyncio.run(_do_report())
        return

    if args.post_weekly:
        async def _do_post() -> None:
            from .report import post_compact_summary
            hours = args.hours or 168
            await post_compact_summary(hours=hours)
        asyncio.run(_do_post())
        return

    if args.post_weekly_global_citations:
        async def _do_post_global() -> None:
            from .report import post_global_citation_summary
            hours = args.hours or 168
            top_n = args.top_n if hasattr(args, 'top_n') and args.top_n else 5
            await post_global_citation_summary(hours=hours, top_n=top_n)
        asyncio.run(_do_post_global())
        return

    if args.post_weekly_per_channel:
        async def _do_post_pc() -> None:
            from .per_channel import (
                post_per_channel_summaries,
                print_per_channel_preview,
                post_per_channel_rollup,
            )
            # Parse optional channel ids
            chans = None
            if args.channels:
                try:
                    chans = [int(x.strip()) for x in args.channels.split(",") if x.strip()]
                except Exception:
                    chans = None
            hours = args.hours or 168
            include_links = not args.no_links
            strategy = args.summary_strategy or ("citations" if args.citations else "citations")
            if args.dry_run:
                await print_per_channel_preview(
                    hours=hours,
                    channels=chans,
                    top_n=args.top_n,
                    min_messages=args.min_messages,
                    max_channels=args.max_channels,
                    sort_by="activity",
                    include_links=include_links,
                    summary_strategy=strategy,
                    verbose=args.verbose,
                )
                return
            if args.post_to == "digest":
                await post_per_channel_rollup(
                    hours=hours,
                    channels=chans,
                    top_n=args.top_n,
                    min_messages=args.min_messages,
                    max_channels=args.max_channels,
                    sort_by="activity",
                    summary_strategy=strategy,
                    verbose=args.verbose,
                    create_in_thread=args.thread,
                    thread_name=args.thread_name,
                )
                print("Posted rollup to digest.")
            else:
                posted = await post_per_channel_summaries(
                    hours=hours,
                    channels=chans,
                    top_n=args.top_n,
                    min_messages=args.min_messages,
                    max_channels=args.max_channels,
                    sort_by="activity",
                    include_links=include_links,
                    summary_strategy=strategy,
                    post_to=args.post_to,
                    verbose=args.verbose,
                )
                print(f"Posted {posted} per-channel summaries to source channels.")
        asyncio.run(_do_post_pc())
        return

    if args.skip_report:
        async def _do_skips() -> None:
            from .db import ensure_schema, connect_client, list_inactive_channels
            await ensure_schema()
            client = await connect_client()
            try:
                cfg = Config.from_env()
                rows = await list_inactive_channels(client, cfg.guild_id)
                if not rows:
                    print("No inactive channels.")
                    return
                print("Inactive (skipped) channels:")
                for ch in rows:
                    nm = ch.name or str(ch.id)
                    print(f"- {nm} [{ch.type}] — {ch.id}")
            finally:
                await client.disconnect()
        asyncio.run(_do_skips())
        return

    if args.sync_threads:
        async def _do_sync_threads() -> None:
            from .threads import sync_threads
            chans = None
            if args.channels:
                try:
                    chans = [int(x.strip()) for x in args.channels.split(",") if x.strip()]
                except Exception:
                    chans = None
            cfg = Config.from_env()
            cnt = await sync_threads(cfg.token, cfg.token_type, cfg.guild_id or 0, parents=chans, verbose=args.verbose)
            print(f"Upserted {cnt} thread channels.")
        asyncio.run(_do_sync_threads())
        return

    if args.sync_threads_archive_all:
        async def _do_sync_threads_all() -> None:
            from .db import ensure_schema, connect_client
            from .threads import sync_threads
            cfg = Config.from_env()
            if cfg.token_type.lower() != "bot":
                print("--sync-threads-archive-all requires DISCORD_TOKEN_TYPE=Bot")
                return
            await ensure_schema()
            client = await connect_client()
            try:
                where = {"isActive": True}
                if cfg.guild_id:
                    where = {"AND": [where, {"guildId": int(cfg.guild_id)}]}
                # Parent channels: text/news only
                parents = await client.channel.find_many(where={"AND": [where, {"type": {"in": ["GUILD_TEXT", "GUILD_NEWS"]}}]})
                parent_ids = [int(ch.id) for ch in parents]
            finally:
                await client.disconnect()
            if not parent_ids:
                print("No parent channels found. Run --sync-channels first.")
                return
            cnt = await sync_threads(cfg.token, cfg.token_type, cfg.guild_id or 0, parents=parent_ids, verbose=args.verbose)
            print(f"Upserted {cnt} thread channels (active + archived across all parents).")
        asyncio.run(_do_sync_threads_all())
        return

    if args.list_threads:
        async def _do_list_threads() -> None:
            from .db import ensure_schema, connect_client
            await ensure_schema()
            client = await connect_client()
            try:
                cfg = Config.from_env()
                filters = []
                if cfg.guild_id:
                    filters.append({"guildId": int(cfg.guild_id)})
                where = {"OR": [
                    {"type": {"contains": "THREAD"}},
                    {"type": {"in": ["10", "11", "12"]}},
                ]}
                if filters:
                    where = {"AND": [where] + filters}
                rows = await client.channel.find_many(where=where, order={"name": "asc"})
                if not rows:
                    print("No thread channels found in DB.")
                    return
                print(f"Threads in DB: {len(rows)}")
                for ch in rows:
                    nm = ch.name or str(ch.id)
                    print(f"- #{nm} ({ch.type}) — {ch.id} parent={ch.parentId}")
            finally:
                await client.disconnect()
        asyncio.run(_do_list_threads())
        return

    if args.threads_report:
        async def _do_thr_report() -> None:
            from .report import print_threads_report
            await print_threads_report(hours=args.hours or 72, verbose=args.verbose)
        asyncio.run(_do_thr_report())
        return

    if args.index_threads_full:
        async def _do_index_threads_full() -> None:
            from .db import ensure_schema, connect_client
            from .indexer import index_messages
            await ensure_schema()
            client = await connect_client()
            try:
                cfg = Config.from_env()
                filters = []
                if cfg.guild_id:
                    filters.append({"guildId": int(cfg.guild_id)})
                where = {"OR": [
                    {"type": {"contains": "THREAD"}},
                    {"type": {"in": ["10", "11", "12"]}},
                ]}
                if filters:
                    where = {"AND": [where] + filters}
                rows = await client.channel.find_many(where=where)
                ids = [int(r.id) for r in rows]
            finally:
                await client.disconnect()
            if not ids:
                print("No thread channels in DB. Run --sync-threads or --sync-threads-archive-all first.")
                return
            per = await index_messages(full=True, channel_ids=ids, verbose=args.verbose, since_dt=None)
            total = sum(per.values())
            print(f"Indexed {total} thread messages into SQLite across {len(ids)} thread channels.")
        asyncio.run(_do_index_threads_full())
        return

    if args.post_test:
        async def _do_test() -> None:
            from .report import post_test_message
            await post_test_message(text=args.text)
        asyncio.run(_do_test())
        return

    if args.post_thread_test:
        async def _do_thread_test() -> None:
            from .config import Config
            from .publish import create_thread, post_one
            cfg = Config.from_env()
            if str(cfg.token_type).lower() != "bot":
                print("--post-thread-test requires DISCORD_TOKEN_TYPE=Bot")
                return
            name = f"Digest Thread Test"
            tid = await create_thread(
                cfg.token,
                cfg.digest_channel_id,
                name=name,
                token_type=cfg.token_type,
                auto_archive_duration=10080,
                thread_type="GUILD_PUBLIC_THREAD",
            )
            if not tid:
                print("Failed to create thread (check permissions/channel type)")
                return
            await post_one(cfg.token, int(tid), "Hello from DiscordDigest thread test! 🚀", token_type=cfg.token_type)
            print(f"Posted thread test to thread {tid} under channel {cfg.digest_channel_id}.")
        asyncio.run(_do_thread_test())
        return

    if args.post_summary_channel:
        async def _do_post_ch() -> None:
            from .report import post_channel_summary
            if not args.channels:
                print("--post-summary-channel requires --channels <channel_id>")
                return
            try:
                cid = int(args.channels.split(",")[0].strip())
            except Exception:
                print("Invalid --channels value")
                return
            await post_channel_summary(channel_id=cid, hours=args.hours or 72)
        asyncio.run(_do_post_ch())
        return
    else:
        asyncio.run(run_preview(dry_run=args.dry_run, hours=args.hours))


if __name__ == "__main__":
    main()
