import datetime as dt
from typing import Dict

from .db import ensure_schema, connect_client
from textwrap import shorten
from .publish import post_text
from .config import Config
from .fetch import fetch_recent_messages
from .scoring import select_top
from .summarize import summarize_with_gemini, naive_extract


async def print_report(hours: int = 72, verbose: bool = False) -> None:
    await ensure_schema()
    client = await connect_client()
    try:
        now = dt.datetime.now(dt.timezone.utc)
        since = now - dt.timedelta(hours=hours)
        msgs = await client.message.find_many(
            where={"createdAt": {"gte": since}},
            order={"createdAt": "desc"},
            include={"channel": True, "author": True},
        )
        if not msgs:
            print(f"No messages in the last {hours}h.")
            return
        per_channel: Dict[int, int] = {}
        per_user: Dict[int, int] = {}
        for m in msgs:
            per_channel[int(m.channelId)] = per_channel.get(int(m.channelId), 0) + 1
            per_user[int(m.authorId)] = per_user.get(int(m.authorId), 0) + 1
        # Resolve names
        channels = await client.channel.find_many(where={"id": {"in": list(per_channel.keys())}})
        users = await client.user.find_many(where={"id": {"in": list(per_user.keys())}})
        cname = {int(c.id): (c.name or str(c.id)) for c in channels}
        uname = {int(u.id): (u.username or str(u.id)) for u in users}

        print(f"Report — last {hours}h")
        print("Top channels:")
        for cid, cnt in sorted(per_channel.items(), key=lambda kv: kv[1], reverse=True)[:10]:
            print(f"- #{cname.get(cid, str(cid))} — {cnt}")
        print("Top users:")
        for uid, cnt in sorted(per_user.items(), key=lambda kv: kv[1], reverse=True)[:10]:
            print(f"- {uname.get(uid, str(uid))} — {cnt}")
        if verbose:
            print("\nSample messages:")
            for m in msgs[:10]:
                print(f"[{m.createdAt:%Y-%m-%d %H:%M}] #{cname.get(int(m.channelId), m.channelId)} by {uname.get(int(m.authorId), m.authorId)}")
                if m.content:
                    print(f"  {m.content[:180]}")
                if m.link:
                    print(f"  {m.link}")
    finally:
        await client.disconnect()


async def build_compact_summary(hours: int = 168, max_lists: int = 5) -> list[str]:
    """Build a compact, postable weekly summary from SQLite data.

    Returns a list of lines suitable for Discord posting (~1800 char blocks will
    be handled by post_text).
    """
    await ensure_schema()
    client = await connect_client()
    lines: list[str] = []
    try:
        now = dt.datetime.now(dt.timezone.utc)
        since = now - dt.timedelta(hours=hours)
        msgs = await client.message.find_many(
            where={"createdAt": {"gte": since}},
            order={"createdAt": "desc"},
            include={"channel": True, "author": True},
        )
        lines.append(f"What's Happening — last {hours//24}d")
        if not msgs:
            lines.append("No recent messages found.")
            return lines
        # Aggregations
        per_channel: dict[int, int] = {}
        per_user: dict[int, int] = {}
        for m in msgs:
            per_channel[int(m.channelId)] = per_channel.get(int(m.channelId), 0) + 1
            per_user[int(m.authorId)] = per_user.get(int(m.authorId), 0) + 1
        # Resolve names
        channels = await client.channel.find_many(where={"id": {"in": list(per_channel.keys())}})
        users = await client.user.find_many(where={"id": {"in": list(per_user.keys())}})
        cname = {int(c.id): (c.name or str(c.id)) for c in channels}
        uname = {int(u.id): (u.username or str(u.id)) for u in users}

        # Top channels / users
        lines.append("")
        lines.append("Top channels:")
        for cid, cnt in sorted(per_channel.items(), key=lambda kv: kv[1], reverse=True)[:max_lists]:
            lines.append(f"- #{cname.get(cid, str(cid))} — {cnt}")
        lines.append("Top users:")
        for uid, cnt in sorted(per_user.items(), key=lambda kv: kv[1], reverse=True)[:max_lists]:
            lines.append(f"- {uname.get(uid, str(uid))} — {cnt}")

        # Highlights: pick messages with content, highest reactions then recency
        content_msgs = [m for m in msgs if m.content]
        content_msgs.sort(key=lambda m: (m.reactionsTotal or 0, m.createdAt), reverse=True)
        highlights = content_msgs[:max_lists]
        if highlights:
            lines.append("")
            lines.append("Highlights:")
            for m in highlights:
                ch = f"#{cname.get(int(m.channelId), m.channelId)}"
                au = uname.get(int(m.authorId), str(m.authorId))
                snippet = shorten(m.content.replace("\n", " ").strip(), width=140, placeholder="…")
                meta = []
                if m.reactionsTotal:
                    meta.append(f"❤ {m.reactionsTotal}")
                if m.attachmentsCount:
                    meta.append(f"📎 {m.attachmentsCount}")
                meta_s = (" "+" ".join(meta)) if meta else ""
                lines.append(f"- {ch} by {au}{meta_s}")
                lines.append(f"  {snippet}")
                if m.link:
                    lines.append(f"  {m.link}")
        return lines
    finally:
        await client.disconnect()


async def post_compact_summary(hours: int = 168) -> None:
    cfg = Config.from_env()
    lines = await build_compact_summary(hours=hours)
    await post_text(cfg.token, cfg.digest_channel_id, lines, token_type=cfg.token_type)


async def print_threads_report(hours: int = 72, verbose: bool = False) -> None:
    await ensure_schema()
    client = await connect_client()
    try:
        now = dt.datetime.now(dt.timezone.utc)
        since = now - dt.timedelta(hours=hours)
        # Only messages in thread channels
        thr_types = ["GUILD_PUBLIC_THREAD", "GUILD_PRIVATE_THREAD", "GUILD_NEWS_THREAD", "10", "11", "12"]
        chans = await client.channel.find_many(where={"type": {"in": thr_types}})
        if not chans:
            print("No thread channels in DB.")
            return
        ch_ids = [int(c.id) for c in chans]
        msgs = await client.message.find_many(
            where={"channelId": {"in": ch_ids}, "createdAt": {"gte": since}},
            order={"createdAt": "desc"},
            include={"channel": True, "author": True},
        )
        if not msgs:
            print(f"No messages in threads for last {hours}h.")
            return
        per_thread: dict[int, int] = {}
        for m in msgs:
            per_thread[int(m.channelId)] = per_thread.get(int(m.channelId), 0) + 1
        name_map = {int(c.id): (c.name or str(c.id)) for c in chans}
        print(f"Threads report — last {hours}h")
        for cid, cnt in sorted(per_thread.items(), key=lambda kv: kv[1], reverse=True)[:10]:
            print(f"- #{name_map.get(cid, str(cid))} — {cnt}")
        if verbose:
            print("\nSample thread messages:")
            for m in msgs[:10]:
                nm = name_map.get(int(m.channelId), str(m.channelId))
                print(f"[{m.createdAt:%Y-%m-%d %H:%M}] #{nm} by {(m.author.username or m.authorId)}")
                if m.content:
                    print(f"  {m.content[:180]}")
    finally:
        await client.disconnect()


async def post_test_message(text: str | None = None) -> None:
    cfg = Config.from_env()
    lines = [text or "Hello from DiscordDigest! 🚀"]
    await post_text(cfg.token, cfg.digest_channel_id, lines, token_type=cfg.token_type)


async def post_channel_summary(channel_id: int, hours: int = 72) -> None:
    cfg = Config.from_env()
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    msgs = await fetch_recent_messages(cfg.token, cfg.token_type, [channel_id], since)
    if not msgs:
        await post_text(cfg.token, cfg.digest_channel_id, [f"No recent messages in channel {channel_id} (last {hours}h)."], token_type=cfg.token_type)
        return
    top = select_top(msgs, 5, now=dt.datetime.now(dt.timezone.utc), window_start=since)
    if cfg.gemini_api_key:
        summary = await summarize_with_gemini(cfg.gemini_api_key, top)
        if not summary or summary.strip() in {"(No summary returned.)"} or summary.lower().startswith("gemini error"):
            summary = naive_extract(top)
    else:
        summary = naive_extract(top)
    title = f"Channel {channel_id} — last {hours}h"
    lines = [f"**{title}**", summary]
    await post_text(cfg.token, cfg.digest_channel_id, lines, token_type=cfg.token_type)
