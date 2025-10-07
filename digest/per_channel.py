import asyncio
import datetime as dt
from typing import Iterable, List, Optional, Tuple, Literal

from .config import Config
from .db import ensure_schema, connect_client
from .fetch import SimpleMessage
from .scoring import select_top
from .summarize import summarize_with_gemini, naive_extract, summarize_with_gemini_citations
from .report import build_inline_citation_summary, build_citations_only
from .publish import post_text, post_one, create_thread


TextableTypes = {
    "GUILD_TEXT",
    "GUILD_NEWS",
    "GUILD_PUBLIC_THREAD",
    "GUILD_PRIVATE_THREAD",
    "GUILD_NEWS_THREAD",
    # Numeric fallbacks observed in DB when type names vary
    "10",
    "11",
    "12",
}


async def _resolve_candidate_channels(client, guild_id: int | None, specified: Optional[List[int]]) -> List[Tuple[int, str]]:
    # returns list of (channelId, name)
    if specified:
        rows = await client.channel.find_many(where={"id": {"in": [int(x) for x in specified]}})
    else:
        where = {"isActive": True}
        if guild_id is not None:
            where = {"AND": [where, {"guildId": int(guild_id)}]}
        rows = await client.channel.find_many(where=where)
    out: List[Tuple[int, str]] = []
    for ch in rows:
        ctype = (ch.type or "").upper()
        if ctype not in TextableTypes:
            continue
        out.append((int(ch.id), ch.name or str(ch.id)))
    return out


async def _load_messages_for_channel(client, channel_id: int, since: dt.datetime) -> List[SimpleMessage]:
    rows = await client.message.find_many(
        where={"channelId": int(channel_id), "createdAt": {"gte": since}},
        order={"createdAt": "desc"},
    )
    out: List[SimpleMessage] = []
    for m in rows:
        out.append(
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
    return out


async def build_per_channel_summaries(
    *,
    hours: int = 168,
    channels: Optional[List[int]] = None,
    top_n: int = 5,
    min_messages: int = 1,
    max_channels: Optional[int] = None,
    sort_by: str = "activity",
    include_links: bool = True,
    summary_strategy: Literal["citations", "gemini", "naive", "gemini_citations"] = "citations",
    verbose: bool = False,
) -> List[Tuple[int, str, List[str]]]:
    """Build per-channel summary lines for recent activity.

    Returns a list of tuples: (channel_id, channel_label, lines).
    """
    await ensure_schema()
    client = await connect_client()
    try:
        cfg = Config.from_env()
        now = dt.datetime.now(dt.timezone.utc)
        since = now - dt.timedelta(hours=hours)

        # Candidate channels
        candidates = await _resolve_candidate_channels(client, cfg.guild_id, channels)
        if not candidates:
            return []

        # Load and summarize per channel
        items: List[Tuple[int, str, int, List[str]]] = []  # (cid, name, count, lines)
        for cid, cname in candidates:
            msgs = await _load_messages_for_channel(client, cid, since)
            if len(msgs) < min_messages:
                if verbose:
                    print(f"[per-channel] skip #{cname} ({cid}): {len(msgs)} msg(s)")
                continue
            # Top selection and summarization
            top = select_top(msgs, top_n, now=now, window_start=since)
            title = f"#{cname} — last {hours // 24}d"
            if summary_strategy == "citations":
                lines: List[str] = build_inline_citation_summary(top, title=title, max_bullets=top_n)
            elif summary_strategy == "gemini":
                summary = await summarize_with_gemini(cfg.gemini_api_key, top) if cfg.gemini_api_key else naive_extract(top)  # type: ignore[arg-type]
                if not summary or summary.strip() in {"(No summary returned.)"} or summary.lower().startswith("gemini error"):
                    summary = naive_extract(top)
                lines = [f"**{title}**", summary]
            elif summary_strategy == "gemini_citations":
                if cfg.gemini_api_key:
                    bullets = await summarize_with_gemini_citations(cfg.gemini_api_key, top, max_bullets=top_n)
                    lines = [f"**{title}**"] + bullets + build_citations_only(top)
                else:
                    # Fallback to deterministic citations
                    lines = build_inline_citation_summary(top, title=title, max_bullets=top_n)
            else:
                summary = naive_extract(top)
                lines = [f"**{title}**", summary]
            if include_links and summary_strategy not in {"citations", "gemini_citations"}:
                # Add top message links as bullets
                links_added = 0
                for m in top:
                    if m.link:
                        lines.append(f"- {m.link}")
                        links_added += 1
                        if links_added >= top_n:
                            break
            items.append((cid, cname, len(msgs), lines))

        # Sort and cap
        if sort_by == "name":
            items.sort(key=lambda x: x[1].lower())
        else:
            # default: by activity (message count desc)
            items.sort(key=lambda x: x[2], reverse=True)
        if max_channels is not None:
            items = items[: max(0, int(max_channels))]

        return [(cid, name, lines) for cid, name, _cnt, lines in items]
    finally:
        await client.disconnect()


async def print_per_channel_preview(
    *,
    hours: int = 168,
    channels: Optional[List[int]] = None,
    top_n: int = 5,
    min_messages: int = 1,
    max_channels: Optional[int] = None,
    sort_by: str = "activity",
    include_links: bool = True,
    summary_strategy: Literal["citations", "gemini", "naive", "gemini_citations"] = "citations",
    verbose: bool = False,
) -> None:
    results = await build_per_channel_summaries(
        hours=hours,
        channels=channels,
        top_n=top_n,
        min_messages=min_messages,
        max_channels=max_channels,
        sort_by=sort_by,
        include_links=include_links,
        summary_strategy=summary_strategy,
        verbose=verbose,
    )
    if not results:
        print(f"No channels with >= {min_messages} messages in last {hours}h.")
        return
    print(f"Per-channel preview — last {hours // 24}d")
    for _cid, name, lines in results:
        print("")
        for ln in lines:
            print(ln)


async def post_per_channel_summaries(
    *,
    hours: int = 168,
    channels: Optional[List[int]] = None,
    top_n: int = 5,
    min_messages: int = 1,
    max_channels: Optional[int] = None,
    sort_by: str = "activity",
    include_links: bool = True,
    summary_strategy: Literal["citations", "gemini", "naive", "gemini_citations"] = "citations",
    post_to: str = "digest",  # 'digest' or 'source'
    rate_limit_sleep: float = 0.5,
    verbose: bool = False,
) -> int:
    cfg = Config.from_env()
    results = await build_per_channel_summaries(
        hours=hours,
        channels=channels,
        top_n=top_n,
        min_messages=min_messages,
        max_channels=max_channels,
        sort_by=sort_by,
        include_links=include_links,
        summary_strategy=summary_strategy,
        verbose=verbose,
    )
    if not results:
        return 0
    posted = 0
    for cid, _name, lines in results:
        target = cfg.digest_channel_id if post_to != "source" else int(cid)
        await post_text(cfg.token, target, lines, token_type=cfg.token_type)
        posted += 1
        if post_to == "source" and rate_limit_sleep > 0:
            await asyncio.sleep(rate_limit_sleep)
    return posted


def _flatten_rollup_lines(results: List[Tuple[int, str, List[str]]]) -> List[str]:
    """Flatten per-channel sections into a single list of lines.

    A blank line is inserted between channel blocks. Use publish.post_text to
    chunk into multiple messages under the platform's character limits.
    """
    out: List[str] = []
    for idx, (_cid, _name, lines) in enumerate(results):
        if idx > 0:
            out.append("")
        out.extend(lines)
    return out


async def post_per_channel_rollup(
    *,
    hours: int = 168,
    channels: Optional[List[int]] = None,
    top_n: int = 5,
    min_messages: int = 1,
    max_channels: Optional[int] = None,
    sort_by: str = "activity",
    summary_strategy: Literal["citations", "gemini", "naive"] = "citations",
    verbose: bool = False,
    thread_name: Optional[str] = None,
    create_in_thread: bool = False,
) -> int:
    """Build per-channel summaries and post as ONE message (rollup)."""
    cfg = Config.from_env()
    results = await build_per_channel_summaries(
        hours=hours,
        channels=channels,
        top_n=top_n,
        min_messages=min_messages,
        max_channels=max_channels,
        sort_by=sort_by,
        include_links=False if summary_strategy == "citations" else True,
        summary_strategy=summary_strategy,
        verbose=verbose,
    )
    if not results:
        return 0
    all_lines = _flatten_rollup_lines(results)
    if create_in_thread:
        # Only Bot tokens can create threads reliably
        if str(cfg.token_type).lower() == "bot":
            tname = thread_name or f"Weekly Digest — {hours//24}d"
            tid = await create_thread(
                cfg.token,
                cfg.digest_channel_id,
                name=tname,
                token_type=cfg.token_type,
                auto_archive_duration=10080,
                thread_type="GUILD_PUBLIC_THREAD",
            )
            if tid:
                await post_text(cfg.token, int(tid), all_lines, token_type=cfg.token_type)
                return 1
        # Fallback to digest channel if thread creation failed
    await post_text(cfg.token, cfg.digest_channel_id, all_lines, token_type=cfg.token_type)
    return 1
