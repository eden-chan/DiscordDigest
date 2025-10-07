import asyncio
import datetime as dt
import json
import os
from pathlib import Path
from typing import Iterable, List, Optional, Dict
import uuid
import re
from urllib.parse import urlparse

from .config import Config
from .fetch import fetch_recent_messages, SimpleMessage
from .db import (
    ensure_schema,
    connect_client,
    list_active_channel_ids,
)


# --- Progress logging (deterministic NDJSON) ---------------------------------
_RUN_ID: Optional[str] = None
_PROGRESS_LOG_PATH: Optional[Path] = None


def _iso(dtobj: Optional[dt.datetime]) -> Optional[str]:
    if not dtobj:
        return None
    if dtobj.tzinfo is None:
        # Treat naive as UTC
        dtobj = dtobj.replace(tzinfo=dt.timezone.utc)
    return dtobj.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _get_run_id() -> str:
    global _RUN_ID
    if _RUN_ID:
        return _RUN_ID
    _RUN_ID = os.getenv("RUN_ID") or uuid.uuid4().hex
    return _RUN_ID


def _get_progress_path() -> Path:
    global _PROGRESS_LOG_PATH
    if _PROGRESS_LOG_PATH is not None:
        return _PROGRESS_LOG_PATH
    p = os.getenv("PROGRESS_LOG_PATH") or os.getenv("PROGRESS_LOG") or str(Path("data") / "backfill_progress.log")
    _PROGRESS_LOG_PATH = Path(p)
    # Ensure directory exists
    _PROGRESS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    return _PROGRESS_LOG_PATH


def _append_progress(event: Dict) -> None:
    """Append a single NDJSON line with stable key ordering.

    - Adds ts and run_id automatically if missing.
    - Uses sort_keys and minimal separators for deterministic formatting.
    """
    try:
        if "ts" not in event:
            event["ts"] = _iso(dt.datetime.now(dt.timezone.utc))
        if "run_id" not in event:
            event["run_id"] = _get_run_id()
        line = json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        with _get_progress_path().open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
    except Exception:
        # Never fail indexing due to logging
        pass


# --- Enrichment helpers (local copies) ---------------------------------------
_USER_MENTION_RE = re.compile(r"<@!?(\d+)>")
_LINK_RE = re.compile(r"https?://[^\s)]+", re.IGNORECASE)


def _extract_user_mentions_from_raw(m, content: str) -> Optional[List[int]]:
    ids: List[int] = []
    try:
        maybe = getattr(m, "mentions", None) or getattr(m, "user_mentions", None)
        if maybe:
            for u in list(maybe):
                try:
                    uid = int(getattr(u, "id", 0))
                    if uid:
                        ids.append(uid)
                except Exception:
                    continue
    except Exception:
        pass
    try:
        for g in _USER_MENTION_RE.findall(content or ""):
            try:
                ids.append(int(g))
            except Exception:
                continue
    except Exception:
        pass
    return list(dict.fromkeys(ids)) or None


def _extract_reply_to_id_from_raw(m) -> Optional[int]:
    try:
        ref = getattr(m, "message_reference", None)
        if ref and getattr(ref, "message_id", None):
            return int(ref.message_id)
    except Exception:
        pass
    try:
        refm = getattr(m, "referenced_message", None)
        if refm and getattr(refm, "id", None):
            return int(refm.id)
    except Exception:
        pass
    return None


def _has_link(content: str) -> Optional[bool]:
    try:
        return bool(_LINK_RE.search(content or ""))
    except Exception:
        return None


def _link_domains(content: str) -> Optional[str]:
    try:
        urls = _LINK_RE.findall(content or "")
        if not urls:
            return None
        domains: List[str] = []
        for u in urls:
            try:
                d = urlparse(u).netloc.lower()
                if d:
                    domains.append(d)
            except Exception:
                continue
        if not domains:
            return None
        uniq = list(dict.fromkeys(domains))
        return ",".join(uniq)
    except Exception:
        return None


def _word_count(content: str) -> Optional[int]:
    try:
        txt = (content or "").strip()
        if not txt:
            return 0
        return len([w for w in re.split(r"\s+", txt) if w])
    except Exception:
        return None


def _has_code_block(content: str) -> Optional[bool]:
    try:
        return "```" in (content or "")
    except Exception:
        return None


def _is_question(content: str) -> Optional[bool]:
    try:
        txt = (content or "").strip()
        if not txt:
            return False
        if txt.endswith("?"):
            return True
        return ("?" in txt) and (len(txt.split()) >= 3)
    except Exception:
        return None


async def _upsert_user(client, *, user_id: int, username: str | None, is_bot: bool | None) -> None:
    await client.user.upsert(
        where={"id": int(user_id)},
        data={
            "create": {"id": int(user_id), "username": username, "bot": bool(is_bot) if is_bot is not None else False},
            "update": {"username": username, "bot": bool(is_bot) if is_bot is not None else False},
        },
    )


async def _upsert_message(client, m: SimpleMessage, guild_id: int | None) -> None:
    await client.message.upsert(
        where={"id": int(m.id)},
        data={
            "create": {
                "id": int(m.id),
                "channelId": int(m.channel_id),
                "guildId": int(guild_id) if guild_id else None,
                "authorId": int(m.author_id),
                "content": m.content or None,
                "createdAt": m.created_at,
                "link": m.link,
                "reactionsTotal": int(m.reactions_total) if m.reactions_total is not None else None,
                "attachmentsCount": int(m.attachments) if m.attachments is not None else None,
                "replyToId": int(m.reply_to_id) if getattr(m, "reply_to_id", None) else None,
                "hasLink": bool(m.has_link) if getattr(m, "has_link", None) is not None else None,
                "linkDomains": getattr(m, "link_domains", None),
                "wordCount": int(m.word_count) if getattr(m, "word_count", None) is not None else None,
                "hasCodeBlock": bool(m.has_code_block) if getattr(m, "has_code_block", None) is not None else None,
                "isQuestion": bool(m.is_question) if getattr(m, "is_question", None) is not None else None,
            },
            "update": {
                "content": m.content or None,
                "reactionsTotal": int(m.reactions_total) if m.reactions_total is not None else None,
                "attachmentsCount": int(m.attachments) if m.attachments is not None else None,
                "replyToId": int(m.reply_to_id) if getattr(m, "reply_to_id", None) else None,
                "hasLink": bool(m.has_link) if getattr(m, "has_link", None) is not None else None,
                "linkDomains": getattr(m, "link_domains", None),
                "wordCount": int(m.word_count) if getattr(m, "word_count", None) is not None else None,
                "hasCodeBlock": bool(m.has_code_block) if getattr(m, "has_code_block", None) is not None else None,
                "isQuestion": bool(m.is_question) if getattr(m, "is_question", None) is not None else None,
            },
        },
    )

    # Replace attachments with latest set
    try:
        await client.messageattachment.delete_many(where={"messageId": int(m.id)})
        if m.attachments_info:
            for att in m.attachments_info:
                await client.messageattachment.create(
                    data={
                        "id": int(att["id"]) if att.get("id") is not None else int(m.id),
                        "messageId": int(m.id),
                        "url": att.get("url") or "",
                        "filename": att.get("filename"),
                        "contentType": att.get("content_type"),
                        "size": int(att["size"]) if att.get("size") is not None else None,
                    }
                )
    except Exception:
        pass

    # Replace reactions with latest set
    try:
        await client.messagereaction.delete_many(where={"messageId": int(m.id)})
        if m.reactions_info:
            for rx in m.reactions_info:
                await client.messagereaction.create(
                    data={
                        "messageId": int(m.id),
                        "emojiId": int(rx["emoji_id"]) if rx.get("emoji_id") is not None else None,
                        "emojiName": rx.get("emoji_name"),
                        "count": int(rx["count"]) if rx.get("count") is not None else None,
                    }
                )
    except Exception:
        pass

    # Replace mentions with latest set (user mentions only)
    try:
        await client.messagemention.delete_many(where={"messageId": int(m.id)})
        if getattr(m, "mentions_user_ids", None):
            for uid in list(dict.fromkeys([int(x) for x in (m.mentions_user_ids or [])])):
                await client.messagemention.create(
                    data={
                        "messageId": int(m.id),
                        "userId": int(uid),
                    }
                )
    except Exception:
        pass


async def index_messages(
    hours: int | None = None,
    channel_ids: Optional[List[int]] = None,
    verbose: bool = False,
    full: bool = False,
    max_total: Optional[int] = None,
    since_dt: Optional[dt.datetime] = None,
    allowed_types: Optional[set[str]] = None,
) -> Dict[int, int]:
    cfg = Config.from_env()
    await ensure_schema()
    ids: List[int] = []
    if channel_ids:
        ids = [int(c) for c in channel_ids]
    else:
        client = await connect_client()
        try:
            ids = await list_active_channel_ids(client, cfg.guild_id)
        finally:
            await client.disconnect()

    if not ids:
        return {}

    total = 0
    per_channel: Dict[int, int] = {}
    lookback = hours if hours is not None else cfg.time_window_hours
    default_since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=lookback)
    # Deterministic processing order
    ids = sorted(int(x) for x in ids)

    # Single connection for entire run to avoid repeated client setup
    client = await connect_client()
    try:
        # Build channel name map for logging and filter to textable types
        rows = await client.channel.find_many(where={"id": {"in": ids}})
        name_map: Dict[int, str] = {int(ch.id): (ch.name or f"{ch.id}") for ch in rows}
        # Only textable channels for now. Default includes threads + news; can be restricted via allowed_types.
        textable = allowed_types or {"GUILD_TEXT", "GUILD_NEWS", "GUILD_PUBLIC_THREAD", "GUILD_PRIVATE_THREAD", "GUILD_NEWS_THREAD", "10", "11", "12"}
        if rows:
            before_filter = set(ids)
            ids = [int(ch.id) for ch in rows if (ch.type or "").upper() in textable]
            skipped = list(before_filter - set(ids))
            if verbose and skipped:
                for cid in skipped:
                    print(f"[skip] channel {cid}: non-textable type ({(name_map.get(cid) or cid)})")

        # Announce log path once
        if verbose:
            print(f"[progress] NDJSON log → {_get_progress_path()} (run_id={_get_run_id()})")

        for cid in ids:
            # Load per-channel state
            state = await client.channelstate.find_unique(where={"channelId": int(cid)})
            if full:
                # Full backfill: page older messages until cutoff/max
                cutoff = since_dt
                cnt = await _backfill_channel(
                    token=cfg.token,
                    token_type=cfg.token_type,
                    channel_id=cid,
                    cutoff=cutoff,
                    max_total=max_total,
                    client=client,
                    verbose=verbose,
                )
                per_channel[int(cid)] = cnt
                total += cnt
                # Only update lastIndexedAt in full mode
                await client.channelstate.upsert(
                    where={"channelId": int(cid)},
                    data={
                        "create": {"channelId": int(cid), "lastIndexedAt": dt.datetime.now(dt.timezone.utc)},
                        "update": {"lastIndexedAt": dt.datetime.now(dt.timezone.utc)},
                    },
                )
                # Log channel completion for backfill
                try:
                    ch = next((r for r in rows if int(r.id) == int(cid)), None)
                    _append_progress(
                        {
                            "mode": "backfill",
                            "status": "done",
                            "guild_id": int(getattr(ch, "guildId", cfg.guild_id or 0)) if ch else (cfg.guild_id or 0),
                            "channel_id": int(cid),
                            "channel_name": name_map.get(int(cid), str(cid)),
                            "type": getattr(ch, "type", None) if ch else None,
                            "parent_id": int(getattr(ch, "parentId", 0)) if ch and getattr(ch, "parentId", None) else None,
                            "total_so_far": int(cnt),
                            "message": "channel backfill complete",
                        }
                    )
                except Exception:
                    pass
                continue
            # Incremental mode
            since = state.lastMessageCreatedAt if state and state.lastMessageCreatedAt else default_since
            msgs: List[SimpleMessage] = await fetch_recent_messages(
                cfg.token,
                cfg.token_type,
                [cid],
                since,
                concurrency=2,
                per_channel_sleep=0.1,
            )
            if not msgs:
                # Update lastIndexedAt even if nothing new
                await client.channelstate.upsert(
                    where={"channelId": int(cid)},
                    data={
                        "create": {"channelId": int(cid), "lastIndexedAt": dt.datetime.now(dt.timezone.utc)},
                        "update": {"lastIndexedAt": dt.datetime.now(dt.timezone.utc)},
                    },
                )
                if verbose:
                    cname = name_map.get(int(cid), str(cid))
                    print(f"[index] {cname} ({cid}): 0 new messages since {since:%Y-%m-%d %H:%M}")
                try:
                    ch = next((r for r in rows if int(r.id) == int(cid)), None)
                    _append_progress(
                        {
                            "mode": "incremental",
                            "status": "ok",
                            "guild_id": int(getattr(ch, "guildId", cfg.guild_id or 0)) if ch else (cfg.guild_id or 0),
                            "channel_id": int(cid),
                            "channel_name": name_map.get(int(cid), str(cid)),
                            "type": getattr(ch, "type", None) if ch else None,
                            "parent_id": int(getattr(ch, "parentId", 0)) if ch and getattr(ch, "parentId", None) else None,
                            "batch_size": 0,
                            "total_so_far": 0,
                            "oldest_seen_iso": None,
                            "before_id": None,
                            "message": f"no new messages since {_iso(since)}",
                        }
                    )
                except Exception:
                    pass
                continue

            # Upsert
            max_created = None
            max_id = None
            oldest_created = None
            for m in msgs:
                if max_created is None or m.created_at > max_created:
                    max_created = m.created_at
                    max_id = m.id
                if oldest_created is None or m.created_at < oldest_created:
                    oldest_created = m.created_at
                await _upsert_user(client, user_id=m.author_id, username=m.author_username, is_bot=m.author_is_bot)
                await _upsert_message(client, m, cfg.guild_id)
                total += 1
            per_channel[int(cid)] = len(msgs)
            if verbose:
                cname = name_map.get(int(cid), str(cid))
                print(f"[index] {cname} ({cid}): +{len(msgs)} messages; last={max_created:%Y-%m-%d %H:%M}")
            # Progress log for incremental batch
            try:
                ch = next((r for r in rows if int(r.id) == int(cid)), None)
                _append_progress(
                    {
                        "mode": "incremental",
                        "status": "ok",
                        "guild_id": int(getattr(ch, "guildId", cfg.guild_id or 0)) if ch else (cfg.guild_id or 0),
                        "channel_id": int(cid),
                        "channel_name": name_map.get(int(cid), str(cid)),
                        "type": getattr(ch, "type", None) if ch else None,
                        "parent_id": int(getattr(ch, "parentId", 0)) if ch and getattr(ch, "parentId", None) else None,
                        "batch_size": int(len(msgs)),
                        "total_so_far": int(len(msgs)),
                        "oldest_seen_iso": _iso(oldest_created),
                        "before_id": None,
                        "message": "incremental batch indexed",
                    }
                )
            except Exception:
                pass
            # Update channel state
            await client.channelstate.upsert(
                where={"channelId": int(cid)},
                data={
                    "create": {
                        "channelId": int(cid),
                        "lastMessageId": int(max_id) if max_id else None,
                        "lastMessageCreatedAt": max_created,
                        "lastIndexedAt": dt.datetime.now(dt.timezone.utc),
                    },
                    "update": {
                        "lastMessageId": int(max_id) if max_id else None,
                        "lastMessageCreatedAt": max_created,
                        "lastIndexedAt": dt.datetime.now(dt.timezone.utc),
                    },
                },
            )
    finally:
        await client.disconnect()
    if verbose:
        print(f"[index] Total indexed: {total}")
    return per_channel


async def _backfill_channel(
    *,
    token: str,
    token_type: str,
    channel_id: int,
    cutoff: Optional[dt.datetime],
    max_total: Optional[int],
    client,
    verbose: bool,
) -> int:
    """Backfill older messages for a single channel until cutoff/max.

    Does not modify ChannelState.lastMessageCreatedAt; caller updates lastIndexedAt.
    """
    import hikari

    rest_app = hikari.RESTApp()
    await rest_app.start()
    count = 0
    # Resume point from ChannelState
    before_id = None
    try:
        state = await client.channelstate.find_unique(where={"channelId": int(channel_id)})
        if state and getattr(state, "backfillBeforeId", None):
            before_id = int(getattr(state, "backfillBeforeId"))
    except Exception:
        pass
    # Channel metadata for logging
    ch_row = None
    try:
        ch_row = await client.channel.find_unique(where={"id": int(channel_id)})
    except Exception:
        ch_row = None
    retries = 0
    try:
        async with rest_app.acquire(token, token_type=token_type) as rest:
            while True:
                try:
                    kw = {}
                    if before_id is not None:
                        kw["before"] = before_id
                    itr = rest.fetch_messages(channel_id, **kw).limit(100)
                    batch: List[hikari.Message] = []
                    async for m in itr:
                        batch.append(m)
                except Exception as e:
                    if verbose:
                        print(f"[skip] channel {channel_id}: fetch failed ({type(e).__name__})")
                    # If missing access, mark channel inactive to skip on future runs
                    try:
                        import hikari
                        if isinstance(e, hikari.errors.ForbiddenError):
                            await client.channel.update(where={"id": int(channel_id)}, data={"isActive": False})
                            _append_progress(
                                {
                                    "mode": "backfill",
                                    "status": "skip_403",
                                    "guild_id": int(getattr(ch_row, "guildId", 0)) if ch_row else None,
                                    "channel_id": int(channel_id),
                                    "channel_name": getattr(ch_row, "name", None) if ch_row else None,
                                    "type": getattr(ch_row, "type", None) if ch_row else None,
                                    "parent_id": int(getattr(ch_row, "parentId", 0)) if ch_row and getattr(ch_row, "parentId", None) else None,
                                    "batch_size": 0,
                                    "total_so_far": int(count),
                                    "oldest_seen_iso": None,
                                    "before_id": int(before_id) if before_id else None,
                                    "message": "Forbidden (403): marked inactive",
                                }
                            )
                            break
                        if hasattr(hikari.errors, "NotFoundError") and isinstance(e, hikari.errors.NotFoundError):
                            # Channel not found; mark inactive so we don't retry next runs
                            await client.channel.update(where={"id": int(channel_id)}, data={"isActive": False})
                            _append_progress(
                                {
                                    "mode": "backfill",
                                    "status": "skip_404",
                                    "guild_id": int(getattr(ch_row, "guildId", 0)) if ch_row else None,
                                    "channel_id": int(channel_id),
                                    "channel_name": getattr(ch_row, "name", None) if ch_row else None,
                                    "type": getattr(ch_row, "type", None) if ch_row else None,
                                    "parent_id": int(getattr(ch_row, "parentId", 0)) if ch_row and getattr(ch_row, "parentId", None) else None,
                                    "batch_size": 0,
                                    "total_so_far": int(count),
                                    "oldest_seen_iso": None,
                                    "before_id": int(before_id) if before_id else None,
                                    "message": "NotFound (404): marked inactive",
                                }
                            )
                            break
                        # Handle 429 rate limiting with retry
                        if hasattr(hikari.errors, "RateLimitedError") and isinstance(e, hikari.errors.RateLimitedError):
                            ra = getattr(e, "retry_after", None)
                            retry_after = float(ra) if ra is not None else 3.0
                            _append_progress(
                                {
                                    "mode": "backfill",
                                    "status": "retry_429",
                                    "guild_id": int(getattr(ch_row, "guildId", 0)) if ch_row else None,
                                    "channel_id": int(channel_id),
                                    "channel_name": getattr(ch_row, "name", None) if ch_row else None,
                                    "type": getattr(ch_row, "type", None) if ch_row else None,
                                    "parent_id": int(getattr(ch_row, "parentId", 0)) if ch_row and getattr(ch_row, "parentId", None) else None,
                                    "batch_size": 0,
                                    "total_so_far": int(count),
                                    "oldest_seen_iso": None,
                                    "before_id": int(before_id) if before_id else None,
                                    "message": f"rate limited; retrying after {retry_after}s",
                                }
                            )
                            await asyncio.sleep(retry_after)
                            continue
                    except Exception:
                        # Unknown exception class or logging failed; apply bounded backoff
                        pass
                    # Generic transient retry with exponential backoff (bounded)
                    retries += 1
                    if retries <= 5:
                        backoff = min(60.0, 1.0 * (2 ** (retries - 1)))
                        _append_progress(
                            {
                                "mode": "backfill",
                                "status": "retry_other",
                                "guild_id": int(getattr(ch_row, "guildId", 0)) if ch_row else None,
                                "channel_id": int(channel_id),
                                "channel_name": getattr(ch_row, "name", None) if ch_row else None,
                                "type": getattr(ch_row, "type", None) if ch_row else None,
                                "parent_id": int(getattr(ch_row, "parentId", 0)) if ch_row and getattr(ch_row, "parentId", None) else None,
                                "batch_size": 0,
                                "total_so_far": int(count),
                                "oldest_seen_iso": None,
                                "before_id": int(before_id) if before_id else None,
                                "message": f"transient error ({type(e).__name__}); retrying after {backoff}s",
                            }
                        )
                        await asyncio.sleep(backoff)
                        continue
                    else:
                        _append_progress(
                            {
                                "mode": "backfill",
                                "status": "error",
                                "guild_id": int(getattr(ch_row, "guildId", 0)) if ch_row else None,
                                "channel_id": int(channel_id),
                                "channel_name": getattr(ch_row, "name", None) if ch_row else None,
                                "type": getattr(ch_row, "type", None) if ch_row else None,
                                "parent_id": int(getattr(ch_row, "parentId", 0)) if ch_row and getattr(ch_row, "parentId", None) else None,
                                "batch_size": 0,
                                "total_so_far": int(count),
                                "oldest_seen_iso": None,
                                "before_id": int(before_id) if before_id else None,
                                "message": f"giving up after {retries} retries ({type(e).__name__})",
                            }
                        )
                        break
                if not batch:
                    break
                # Process oldest -> newest within page for determinism
                try:
                    batch.sort(
                        key=lambda x: getattr(x, "created_at", dt.datetime.fromtimestamp(0, tz=dt.timezone.utc))
                    )
                except Exception:
                    pass
                earliest = batch[0]
                earliest_ts = getattr(earliest, "created_at", None)
                for m in batch:
                    ts = getattr(m, "created_at", None)
                    if cutoff and ts and ts < cutoff:
                        return count
                    content = m.content or ""
                    link = f"https://discord.com/channels/@me/{m.channel_id}/{m.id}"
                    if getattr(m, "guild_id", None):
                        link = f"https://discord.com/channels/{m.guild_id}/{m.channel_id}/{m.id}"
                    # reactions
                    total_reacts = 0
                    reactions_info: List[dict] = []
                    try:
                        if m.reactions:
                            for r in m.reactions:
                                try:
                                    total_reacts += int(getattr(r, "count", 0))
                                    emoji = getattr(r, "emoji", None)
                                    emoji_id = int(getattr(emoji, "id", 0)) if emoji and getattr(emoji, "id", None) else None
                                    emoji_name = getattr(emoji, "name", None)
                                    reactions_info.append({"emoji_id": emoji_id, "emoji_name": emoji_name, "count": int(getattr(r, "count", 0))})
                                except Exception:
                                    continue
                    except Exception:
                        pass
                    # attachments
                    attachments = 0
                    attachments_info: List[dict] = []
                    try:
                        if m.attachments:
                            attachments = len(m.attachments)
                            for att in m.attachments:
                                try:
                                    attachments_info.append({
                                        "id": int(getattr(att, "id", 0)) if getattr(att, "id", None) else None,
                                        "url": str(getattr(att, "url", "")),
                                        "filename": getattr(att, "filename", None),
                                        "content_type": getattr(att, "media_type", None) or getattr(att, "content_type", None),
                                        "size": int(getattr(att, "size", 0)) if getattr(att, "size", None) else None,
                                    })
                                except Exception:
                                    continue
                    except Exception:
                        attachments = 0

                    sm = SimpleMessage(
                        id=int(m.id),
                        channel_id=int(m.channel_id),
                        author_id=int(m.author.id) if m.author else 0,
                        created_at=ts or dt.datetime.now(dt.timezone.utc),
                        content=content,
                        link=link,
                        author_username=str(getattr(m.author, "username", None)) if m.author else None,
                        author_is_bot=bool(getattr(m.author, "is_bot", False)) if m.author else None,
                        reactions_total=total_reacts,
                        attachments=attachments,
                        attachments_info=attachments_info or None,
                        reactions_info=reactions_info or None,
                        mentions_user_ids=_extract_user_mentions_from_raw(m, content),
                        reply_to_id=_extract_reply_to_id_from_raw(m),
                        has_link=_has_link(content),
                        link_domains=_link_domains(content),
                        word_count=_word_count(content),
                        has_code_block=_has_code_block(content),
                        is_question=_is_question(content),
                    )
                    await _upsert_user(client, user_id=sm.author_id, username=sm.author_username, is_bot=sm.author_is_bot)
                    await _upsert_message(client, sm, None)
                    count += 1
                    if max_total is not None and count >= max_total:
                        return count
                # Page older: use earliest message id as the next before pointer
                before_id = int(earliest.id)
                try:
                    await client.channelstate.upsert(
                        where={"channelId": int(channel_id)},
                        data={
                            "create": {"channelId": int(channel_id), "backfillBeforeId": before_id, "backfillOldestAt": earliest_ts},
                            "update": {"backfillBeforeId": before_id, "backfillOldestAt": earliest_ts},
                        },
                    )
                except Exception:
                    pass
                # Reset transient retry counter after a successful batch
                retries = 0
                if verbose:
                    try:
                        when = earliest_ts.strftime("%Y-%m-%d %H:%M") if earliest_ts else "?"
                    except Exception:
                        when = "?"
                    print(f"[backfill] channel {channel_id}: total {count}, oldest seen {when}")
                # Progress log for this batch
                try:
                    _append_progress(
                        {
                            "mode": "backfill",
                            "status": "ok",
                            "guild_id": int(getattr(ch_row, "guildId", 0)) if ch_row else None,
                            "channel_id": int(channel_id),
                            "channel_name": getattr(ch_row, "name", None) if ch_row else None,
                            "type": getattr(ch_row, "type", None) if ch_row else None,
                            "parent_id": int(getattr(ch_row, "parentId", 0)) if ch_row and getattr(ch_row, "parentId", None) else None,
                            "batch_size": int(len(batch)),
                            "total_so_far": int(count),
                            "oldest_seen_iso": _iso(earliest_ts),
                            "before_id": int(before_id),
                            "message": "backfill batch indexed",
                        }
                    )
                except Exception:
                    pass
                # Gentle pacing between pages to reduce rate limiting
                await asyncio.sleep(0.25)
    finally:
        await rest_app.close()
    return count
