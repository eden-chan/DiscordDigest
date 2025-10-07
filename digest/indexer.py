import asyncio
import datetime as dt
from typing import Iterable, List, Optional, Dict

from .config import Config
from .fetch import fetch_recent_messages, SimpleMessage
from .db import (
    ensure_schema,
    connect_client,
    list_active_channel_ids,
)


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
            },
            "update": {
                "content": m.content or None,
                "reactionsTotal": int(m.reactions_total) if m.reactions_total is not None else None,
                "attachmentsCount": int(m.attachments) if m.attachments is not None else None,
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


async def index_messages(
    hours: int | None = None,
    channel_ids: Optional[List[int]] = None,
    verbose: bool = False,
    full: bool = False,
    max_total: Optional[int] = None,
    since_dt: Optional[dt.datetime] = None,
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

    # Single connection for entire run to avoid repeated client setup
    client = await connect_client()
    try:
        # Build channel name map for logging and filter to textable types
        rows = await client.channel.find_many(where={"id": {"in": ids}})
        name_map: Dict[int, str] = {int(ch.id): (ch.name or f"{ch.id}") for ch in rows}
        # Only textable channels for now (include threads)
        textable = {"GUILD_TEXT", "GUILD_NEWS", "GUILD_PUBLIC_THREAD", "GUILD_PRIVATE_THREAD", "GUILD_NEWS_THREAD", "10", "11", "12"}
        if rows:
            before_filter = set(ids)
            ids = [int(ch.id) for ch in rows if (ch.type or "").upper() in textable]
            skipped = list(before_filter - set(ids))
            if verbose and skipped:
                for cid in skipped:
                    print(f"[skip] channel {cid}: non-textable type ({(name_map.get(cid) or cid)})")

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
                continue
            # Incremental mode
            since = state.lastMessageCreatedAt if state and state.lastMessageCreatedAt else default_since
            msgs: List[SimpleMessage] = await fetch_recent_messages(cfg.token, cfg.token_type, [cid], since)
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
                continue

            # Upsert
            max_created = None
            max_id = None
            for m in msgs:
                if max_created is None or m.created_at > max_created:
                    max_created = m.created_at
                    max_id = m.id
                await _upsert_user(client, user_id=m.author_id, username=m.author_username, is_bot=m.author_is_bot)
                await _upsert_message(client, m, cfg.guild_id)
                total += 1
            per_channel[int(cid)] = len(msgs)
            if verbose:
                cname = name_map.get(int(cid), str(cid))
                print(f"[index] {cname} ({cid}): +{len(msgs)} messages; last={max_created:%Y-%m-%d %H:%M}")
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
    before_id = None
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
                    except Exception:
                        pass
                    break
                if not batch:
                    break
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
                    )
                    await _upsert_user(client, user_id=sm.author_id, username=sm.author_username, is_bot=sm.author_is_bot)
                    await _upsert_message(client, sm, None)
                    count += 1
                    if max_total is not None and count >= max_total:
                        return count
                before_id = int(batch[-1].id)
                if verbose:
                    print(f"[backfill] channel {channel_id}: total {count}, oldest seen {batch[-1].created_at:%Y-%m-%d %H:%M}")
    finally:
        await rest_app.close()
    return count
