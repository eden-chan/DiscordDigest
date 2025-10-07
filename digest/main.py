import asyncio
import datetime as dt
import argparse
import os
import json

from dotenv import load_dotenv
from .config import Config
from .fetch import fetch_recent_messages
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


def _parse_static_channel_ids(data: object) -> list[int]:
    """Parse channel IDs from common JSON shapes.

    Accepts:
    - {"channels": [{"id": ...}, ...]}
    - {"items": [{"id": ...}, ...]}
    - {"data": {"channels": [...]}}
    - Top-level list of channel dicts
    """
    def norm_id(v):
        try:
            return int(v)
        except Exception:
            return None

    arr = None
    if isinstance(data, list):
        arr = data
    elif isinstance(data, dict):
        for key in ("channels", "items"):
            maybe = data.get(key)
            if isinstance(maybe, list):
                arr = maybe
                break
        if arr is None and isinstance(data.get("data"), dict):
            inner = data.get("data")
            maybe = inner.get("channels") if isinstance(inner, dict) else None
            if isinstance(maybe, list):
                arr = maybe
        if arr is None and isinstance(data.get("guild"), dict):
            inner = data.get("guild")
            maybe = inner.get("channels") if isinstance(inner, dict) else None
            if isinstance(maybe, list):
                arr = maybe
    if not isinstance(arr, list):
        return []
    ids: list[int] = []
    for ch in arr:
        if not isinstance(ch, dict):
            continue
        cid = ch.get("id") or ch.get("channel_id") or ch.get("channelId")
        cid = norm_id(cid)
        if cid is not None:
            ids.append(cid)
    return ids


def _parse_static_channels_with_labels(data: object) -> list[tuple[int, str]]:
    """Parse (id, label) pairs from common JSON shapes.

    Accepts:
    - {"channels": [{"id": ..., "label": ..., "name": ...}, ...]}
    - {"items": [...]}
    - {"data": {"channels": [...]}}
    - Top-level list of channel dicts
    """
    def norm_id(v):
        try:
            return int(v)
        except Exception:
            return None

    arr = None
    if isinstance(data, list):
        arr = data
    elif isinstance(data, dict):
        for key in ("channels", "items"):
            maybe = data.get(key)
            if isinstance(maybe, list):
                arr = maybe
                break
        if arr is None and isinstance(data.get("data"), dict):
            inner = data.get("data")
            maybe = inner.get("channels") if isinstance(inner, dict) else None
            if isinstance(maybe, list):
                arr = maybe
        if arr is None and isinstance(data.get("guild"), dict):
            inner = data.get("guild")
            maybe = inner.get("channels") if isinstance(inner, dict) else None
            if isinstance(maybe, list):
                arr = maybe
    if not isinstance(arr, list):
        return []
    out: list[tuple[int, str]] = []
    for ch in arr:
        if not isinstance(ch, dict):
            continue
        cid = ch.get("id") or ch.get("channel_id") or ch.get("channelId")
        cid = norm_id(cid)
        if cid is None:
            continue
        name = ch.get("name") or ch.get("channel_name")
        label = ch.get("label")
        if not label:
            label = f"#{name} — {cid}" if name else f"Channel {cid}"
        out.append((cid, str(label)))
    return out


async def run_preview(dry_run: bool = False, hours: int | None = None) -> None:
    cfg = Config.from_env()

    lookback = hours if hours is not None else cfg.time_window_hours
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=lookback)

    # Prefer DB channels; fallback to JSON if DB empty
    channel_ids: list[int] = []
    try:
        from .db import connect_client, list_active_channel_ids, ensure_schema

        await ensure_schema()
        client = await connect_client()
        try:
            channel_ids = await list_active_channel_ids(client, cfg.guild_id)
        finally:
            await client.disconnect()
    except Exception:
        channel_ids = []
    if not channel_ids:
        static_path = os.path.join("data", "channels.json")
        try:
            if os.path.exists(static_path):
                with open(static_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                channel_ids = _parse_static_channel_ids(data)
        except Exception:
            channel_ids = []
    if not channel_ids:
        msg = (
            "No channels available. Sync with `python -m digest --sync-channels` (Bot token), or provide data/channels.json."
        )
        print(msg)
        return

    all_messages = await fetch_recent_messages(
        cfg.token, cfg.token_type, channel_ids, since
    )
    now = dt.datetime.now(dt.timezone.utc)
    if not all_messages:
        if dry_run:
            print("No recent messages found.")
        else:
            await post_text(cfg.token, cfg.digest_channel_id, ["No recent messages found."])
        return

    top_messages = select_top(all_messages, cfg.top_n, now=now, window_start=since)

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
        await post_text(cfg.token, cfg.digest_channel_id, lines)


async def run_list_channels(live: bool = False) -> None:
    # List channels from SQLite by default, optionally adding live REST listing.
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
        # Fallback to JSON if DB empty
        try:
            static_path = os.path.join("data", "channels.json")
            if os.path.exists(static_path):
                with open(static_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                pairs = _parse_static_channels_with_labels(data)
                if pairs:
                    print(f"Channels from {static_path}:")
                    for _cid, label in pairs:
                        print(f"- {label}")
                    return
        except Exception:
            pass
        print("No channels in DB or JSON. Run `python -m digest --sync-channels` (Bot token).")
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
    parser.add_argument("--list-channels", action="store_true", help="List channels and exit (reads data/channels.json by default)")
    parser.add_argument("--live", action="store_true", help="With --list-channels, fetch live via REST (Bot token)")
    parser.add_argument("--sync-channels", action="store_true", help="Upsert live channels into SQLite (Bot token)")
    parser.add_argument("--list-db-channels", action="store_true", help="List channels stored in SQLite")
    parser.add_argument("--guild", type=int, help="Override guild id for DB/list operations")
    parser.add_argument("--seed-channels-from-json", action="store_true", help="Upsert channels from a JSON file into SQLite")
    parser.add_argument("--json-path", default=os.path.join("data", "channels.json"), help="Path to channels JSON for --seed-channels-from-json")
    parser.add_argument("--index-messages", action="store_true", help="Index recent messages from active channels into SQLite")
    parser.add_argument("--channels", help="Comma-separated channel IDs for indexing (optional)")
    parser.add_argument("--verbose", action="store_true", help="Verbose output for indexing/reporting")
    parser.add_argument("--report", action="store_true", help="Print a quick report from SQLite for the time window")
    parser.add_argument("--full", action="store_true", help="Full backfill mode (requires --channels)")
    parser.add_argument("--max", type=int, help="Max messages per channel in full mode")
    parser.add_argument("--since", help="ISO timestamp cutoff for full mode (e.g., 2024-01-01T00:00:00Z)")
    parser.add_argument("--post-weekly", action="store_true", help="Post a compact weekly summary to the configured digest channel")
    parser.add_argument("--skip-report", action="store_true", help="List channels marked inactive (e.g., due to 403)")
    parser.add_argument("--sync-threads", action="store_true", help="Discover and upsert thread channels for the guild")
    parser.add_argument("--list-threads", action="store_true", help="List discovered thread channels from SQLite")
    parser.add_argument("--threads-report", action="store_true", help="Print a threads-only report")
    parser.add_argument("--post-test", action="store_true", help="Post a test message to the digest channel")
    parser.add_argument("--text", help="Text for --post-test")
    parser.add_argument("--post-summary-channel", action="store_true", help="Post a summary of a single channel to the digest channel")
    parser.add_argument("--dry-run", action="store_true", help="Print digest to stdout without posting")
    parser.add_argument("--hours", type=int, help="Override lookback window in hours")
    parser.add_argument("--oauth-exchange", action="store_true", help="Exchange OAUTH_CODE for tokens using env vars")
    parser.add_argument("--code", help="Authorization code for --oauth-exchange (overrides OAUTH_CODE env)")
    parser.add_argument("--oauth-refresh", action="store_true", help="Refresh access token using env vars")
    parser.add_argument("--out", help="Write resulting JSON to a file (with --oauth-*)")
    parser.add_argument("--oauth-login", action="store_true", help="Start a local server to capture code and exchange automatically")
    parser.add_argument("--no-browser", action="store_true", help="Do not open browser automatically for --oauth-login")
    parser.add_argument("--timeout", type=int, default=300, help="Timeout in seconds for OAuth login code capture")
    parser.add_argument("--oauth-probe", action="store_true", help="Probe current token and print scopes/identity")
    parser.add_argument("--oauth-refresh-update-channels-json", action="store_true", help="With --oauth-refresh, also update top-level tokens inside data/channels.json")
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
        # Optionally merge into data/channels.json (top-level token fields)
        if args.oauth_refresh and args.oauth_refresh_update_channels_json:
            try:
                ch_path = os.path.join("data", "channels.json")
                if os.path.exists(ch_path):
                    with open(ch_path, "r", encoding="utf-8") as f:
                        doc = json.load(f)
                    if isinstance(doc, dict):
                        doc["access_token"] = result.get("access_token", doc.get("access_token"))
                        doc["token_type"] = result.get("token_type", doc.get("token_type"))
                        if result.get("refresh_token"):
                            doc["refresh_token"] = result["refresh_token"]
                        with open(ch_path, "w", encoding="utf-8") as f:
                            json.dump(doc, f, ensure_ascii=False, indent=2)
                            f.write("\n")
                        print("Updated data/channels.json with refreshed tokens.")
            except Exception as e:
                print(f"Warning: failed to update data/channels.json: {e}")
        return

    if args.list_channels:
        asyncio.run(run_list_channels(live=args.live))
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

    if args.seed_channels_from_json:
        from .db import connect_client, upsert_guild, upsert_channels, ensure_schema

        # Load JSON
        try:
            with open(args.json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"Failed to load {args.json_path}: {e}")
            return

        # Determine guild id
        gid = None
        if isinstance(data, dict):
            gid = data.get("guild_id")
            if not gid and isinstance(data.get("guild"), dict):
                try:
                    gid = int(data["guild"].get("id"))
                except Exception:
                    gid = None
        if not gid:
            cfg = Config.from_env()
            gid = args.guild or cfg.guild_id
        if not gid:
            print("A guild id is required (provide in JSON as guild_id or via --guild).")
            return

        # Build upsert tuples
        pairs = _parse_static_channels_with_labels(data)
        if not pairs:
            ids = _parse_static_channel_ids(data)
            pairs = [(cid, None) for cid in ids]
        if not pairs:
            print("No channels found in JSON.")
            return
        items = [(cid, (label.split(" — ")[0].lstrip("#") if label else None), None, None, None, None, None, 1) for cid, label in pairs]

        asyncio.run(ensure_schema())
        client = asyncio.run(connect_client())
        try:
            asyncio.run(upsert_guild(client, int(gid)))
            asyncio.run(upsert_channels(client, int(gid), items))
            print(f"Seeded {len(items)} channels into SQLite from {args.json_path}.")
        finally:
            asyncio.run(client.disconnect())
        return

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
            per = await index_messages(
                hours=args.hours,
                channel_ids=chans,
                verbose=args.verbose,
                full=args.full,
                max_total=args.max,
                since_dt=since_dt,
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

    if args.post_test:
        async def _do_test() -> None:
            from .report import post_test_message
            await post_test_message(text=args.text)
        asyncio.run(_do_test())
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
