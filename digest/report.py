import datetime as dt
from typing import Dict, Iterable, List, Optional, Tuple

from .db import ensure_schema, connect_client
from textwrap import shorten
from .publish import post_text
from .config import Config
from .fetch import fetch_recent_messages, SimpleMessage
from .scoring import select_top
from .summarize import summarize_with_gemini, naive_extract, summarize_with_gemini_citations


async def print_report(hours: int = 72, verbose: bool = False) -> None:
    snap = await build_activity_snapshot(hours=hours, max_highlights=10)
    channels = snap.get("channels", [])
    users = snap.get("users", [])
    if not channels and not users:
        print(f"No messages in the last {hours}h.")
        return
    print(f"Report — last {hours}h")
    if channels:
        print("Top channels:")
        for row in channels[:10]:
            nm = row.get("name") or str(row.get("id"))
            print(f"- #{nm} — {row.get('count')}")
    if users:
        print("Top users:")
        for row in users[:10]:
            nm = row.get("username") or str(row.get("id"))
            print(f"- {nm} — {row.get('count')}")
    if verbose:
        highlights = snap.get("highlights", [])
        if highlights:
            print("\nHighlights:")
            for m in highlights[:10]:
                ch = m.get("channel") or str(m.get("channelId"))
                au = m.get("author") or str(m.get("authorId"))
                created = m.get("createdAt")
                ts = created.strftime("%Y-%m-%d %H:%M") if created else ""
                print(f"[{ts}] #{ch} by {au}")
                content = m.get("content") or ""
                if content:
                    print(f"  {content[:180]}")
                link = m.get("link")
                if link:
                    print(f"  {link}")


async def build_activity_snapshot(
    *,
    hours: int = 72,
    max_highlights: int = 5,
) -> dict:
    """Return a deterministic activity snapshot for the window.

    Shape:
    {
      'since': datetime,
      'until': datetime,
      'channels': [{'id': int, 'name': str|None, 'count': int}],
      'users': [{'id': int, 'username': str|None, 'count': int}],
      'highlights': [{'id': int, 'link': str|None, 'channelId': int, 'channel': str|None,
                      'authorId': int, 'author': str|None, 'reactions': int|None,
                      'attachments': int|None, 'content': str|None, 'createdAt': datetime}],
    }
    """
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
        per_channel: Dict[int, int] = {}
        per_user: Dict[int, int] = {}
        for m in msgs:
            per_channel[int(m.channelId)] = per_channel.get(int(m.channelId), 0) + 1
            per_user[int(m.authorId)] = per_user.get(int(m.authorId), 0) + 1
        # Resolve names
        channels = await client.channel.find_many(where={"id": {"in": list(per_channel.keys())}})
        users = await client.user.find_many(where={"id": {"in": list(per_user.keys())}})
        cname = {int(c.id): (c.name or None) for c in channels}
        uname = {int(u.id): (u.username or None) for u in users}

        channel_list = [
            {"id": cid, "name": cname.get(cid), "count": cnt}
            for cid, cnt in sorted(per_channel.items(), key=lambda kv: kv[1], reverse=True)
        ]
        user_list = [
            {"id": uid, "username": uname.get(uid), "count": cnt}
            for uid, cnt in sorted(per_user.items(), key=lambda kv: kv[1], reverse=True)
        ]

        # Deterministic highlights: content-bearing messages sorted by reactions desc then recency
        content_msgs = [m for m in msgs if m.content]
        content_msgs.sort(key=lambda m: (m.reactionsTotal or 0, m.createdAt), reverse=True)
        highlights = []
        for m in content_msgs[: max(0, max_highlights)]:
            highlights.append(
                {
                    "id": int(m.id),
                    "link": m.link,
                    "channelId": int(m.channelId),
                    "channel": (m.channel.name if getattr(m, "channel", None) else None) or None,
                    "authorId": int(m.authorId),
                    "author": (m.author.username if getattr(m, "author", None) else None) or None,
                    "reactions": m.reactionsTotal,
                    "attachments": m.attachmentsCount,
                    "content": m.content,
                    "createdAt": m.createdAt,
                }
            )

        return {
            "since": since,
            "until": now,
            "channels": channel_list,
            "users": user_list,
            "highlights": highlights,
        }
    finally:
        await client.disconnect()


def build_inline_citation_summary(
    messages: Iterable[SimpleMessage],
    *,
    title: Optional[str] = None,
    max_bullets: int = 5,
) -> List[str]:
    """Compose a summary with inline citations from given messages.

    Output format:
    [Title]
    - Bullet 1 [1]
    - Bullet 2 [2]
    ...
    Citations:
    [1] <link> — optional meta
    [2] <link> — optional meta
    """
    # Pick top items in given order; if more than max_bullets, trim deterministically
    items: List[SimpleMessage] = list(messages)
    # Use most recent first within selection
    items.sort(key=lambda m: m.created_at, reverse=True)
    items = items[: max(0, max_bullets)]

    lines: List[str] = []
    if title:
        lines.append(f"**{title}**")
    # Bullets with inline [n]
    for i, m in enumerate(items, start=1):
        snippet = shorten((m.content or "").replace("\n", " ").strip(), width=140, placeholder="…")
        if not snippet:
            snippet = "(link)"
        lines.append(f"- {snippet} [{i}]")

    if not items:
        return lines or ["(No items to summarize.)"]

    lines.append("")
    lines.append("Citations:")
    for i, m in enumerate(items, start=1):
        meta: List[str] = []
        if m.reactions_total:
            meta.append(f"❤ {m.reactions_total}")
        if m.attachments:
            meta.append(f"📎 {m.attachments}")
        metas = (" "+" ".join(meta)) if meta else ""
        link = m.link or ""
        lines.append(f"[{i}] {link}{metas}")
    return lines


def build_citations_only(messages: Iterable[SimpleMessage]) -> List[str]:
    """Build only the numbered citations block for the given messages."""
    items: List[SimpleMessage] = list(messages)
    if not items:
        return []
    lines: List[str] = ["", "Citations:"]
    for i, m in enumerate(items, start=1):
        meta: List[str] = []
        if m.reactions_total:
            meta.append(f"❤ {m.reactions_total}")
        if m.attachments:
            meta.append(f"📎 {m.attachments}")
        metas = (" "+" ".join(meta)) if meta else ""
        link = m.link or ""
        lines.append(f"[{i}] {link}{metas}")
    return lines


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
    """Post a single-channel summary strictly from SQLite data.

    Uses the per-channel builder and citation-style summary for determinism.
    """
    from .per_channel import post_per_channel_summaries

    await post_per_channel_summaries(
        hours=hours,
        channels=[int(channel_id)],
        top_n=5,
        summary_strategy="citations",
        post_to="digest",
    )


async def build_global_citation_summary(hours: int = 168, top_n: int = 5) -> list[str]:
    """Build a concise global summary using Gemini bullets + numbered citations."""
    await ensure_schema()
    client = await connect_client()
    try:
        now = dt.datetime.now(dt.timezone.utc)
        since = now - dt.timedelta(hours=hours)
        # Pull recent content-bearing messages
        rows = await client.message.find_many(
            where={"createdAt": {"gte": since}, "content": {"not": None}},
            order={"createdAt": "desc"},
        )
        if not rows:
            return [f"Weekly Highlights — last {hours//24}d", "No recent messages found."]
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
        top = select_top(msgs, top_n, now=now, window_start=since)
        cfg = Config.from_env()
        title = f"Weekly Highlights — last {hours//24}d"
        if cfg.gemini_api_key:
            bullets = await summarize_with_gemini_citations(cfg.gemini_api_key, top, max_bullets=top_n, max_chars=600)
            lines = [f"**{title}**"] + bullets + build_citations_only(top)
        else:
            lines = build_inline_citation_summary(top, title=title, max_bullets=top_n)
        return lines
    finally:
        await client.disconnect()


async def post_global_citation_summary(hours: int = 168, top_n: int = 5) -> None:
    cfg = Config.from_env()
    lines = await build_global_citation_summary(hours=hours, top_n=top_n)
    await post_text(cfg.token, cfg.digest_channel_id, lines, token_type=cfg.token_type)
