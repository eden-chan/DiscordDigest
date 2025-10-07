import asyncio
import datetime as dt
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Optional

import hikari
import os


@dataclass
class SimpleMessage:
    id: int
    channel_id: int
    author_id: int
    created_at: dt.datetime
    content: str
    link: str
    author_username: Optional[str] = None
    author_is_bot: Optional[bool] = None
    reactions_total: int = 0
    attachments: int = 0
    attachments_info: Optional[List[dict]] = None
    reactions_info: Optional[List[dict]] = None


async def fetch_recent_messages(
    token: str,
    token_type: str,
    channel_ids: Iterable[int],
    since: dt.datetime,
    limit_per_channel: int = 200,
    *,
    concurrency: int = 2,
    per_channel_sleep: float = 0.0,
) -> List[SimpleMessage]:
    """Fetch recent messages from the given channel IDs using Hikari REST.

    Notes:
        - Keeps it simple: top-level channels only (no threads in v1).
        - Applies a soft limit and filters by timestamp client-side.
    """

    rest_app = hikari.RESTApp()
    await rest_app.start()

    out: List[SimpleMessage] = []
    try:
        async with rest_app.acquire(token, token_type=token_type) as rest:
            # Concurrency is rate-limit sensitive; default to 2 to be gentle
            sem = asyncio.Semaphore(max(1, int(concurrency)))

            async def fetch_one(cid: int) -> None:
                async with sem:
                    import random
                    from hikari import errors as _hikari_errors
                    retries = 0
                    while True:
                        try:
                            itr = rest.fetch_messages(cid).limit(min(100, limit_per_channel))
                            async for m in itr:
                                # created_at is aware datetime
                                ts = getattr(m, "created_at", None)
                                if not ts or ts < since:
                                    continue
                            content = m.content or ""
                            msg_link = f"https://discord.com/channels/@me/{m.channel_id}/{m.id}"
                            # If in guild, prefer guild path
                            if getattr(m, "guild_id", None):
                                msg_link = f"https://discord.com/channels/{m.guild_id}/{m.channel_id}/{m.id}"
                            # reactions
                            total_reacts = 0
                            try:
                                if m.reactions:
                                    for r in m.reactions:
                                        try:
                                            total_reacts += int(getattr(r, "count", 0))
                                        except Exception:
                                            pass
                            except Exception:
                                pass

                            attachments = 0
                            attachments_info: List[dict] = []
                            try:
                                if m.attachments:
                                    attachments = len(m.attachments)
                                    for att in m.attachments:
                                        try:
                                            attachments_info.append(
                                                {
                                                    "id": int(getattr(att, "id", 0)) if getattr(att, "id", None) else None,
                                                    "url": str(getattr(att, "url", "")),
                                                    "filename": getattr(att, "filename", None),
                                                    "content_type": getattr(att, "media_type", None) or getattr(att, "content_type", None),
                                                    "size": int(getattr(att, "size", 0)) if getattr(att, "size", None) else None,
                                                }
                                            )
                                        except Exception:
                                            continue
                            except Exception:
                                attachments = 0

                            reactions_info: List[dict] = []
                            try:
                                if m.reactions:
                                    for r in m.reactions:
                                        try:
                                            emoji = getattr(r, "emoji", None)
                                            emoji_id = int(getattr(emoji, "id", 0)) if emoji and getattr(emoji, "id", None) else None
                                            emoji_name = getattr(emoji, "name", None)
                                            reactions_info.append(
                                                {
                                                    "emoji_id": emoji_id,
                                                    "emoji_name": emoji_name,
                                                    "count": int(getattr(r, "count", 0)),
                                                }
                                            )
                                        except Exception:
                                            continue
                            except Exception:
                                pass

                            out.append(
                                SimpleMessage(
                                    id=int(m.id),
                                    channel_id=int(m.channel_id),
                                    author_id=int(m.author.id) if m.author else 0,
                                    author_username=str(getattr(m.author, "username", None)) if m.author else None,
                                    author_is_bot=bool(getattr(m.author, "is_bot", False)) if m.author else None,
                                    created_at=ts,
                                    content=content,
                                    link=msg_link,
                                    reactions_total=total_reacts,
                                    attachments=attachments,
                                    attachments_info=attachments_info or None,
                                    reactions_info=reactions_info or None,
                                )
                            )
                            break
                        except _hikari_errors.ForbiddenError:
                            # Missing Access: skip this channel gracefully
                            if os.getenv("DIGEST_DEBUG"):
                                print(f"fetch_messages forbidden for channel {cid}: 403 Missing Access")
                            break
                        except Exception as e:
                            # Bound retries if the exception exposes retry_after; otherwise bail out
                            ra = getattr(e, "retry_after", None)
                            if ra is not None and retries < 5:
                                try:
                                    await asyncio.sleep(float(ra) + random.uniform(0.1, 0.5))
                                except Exception:
                                    await asyncio.sleep(1.0)
                                retries += 1
                                continue
                            if os.getenv("DIGEST_DEBUG"):
                                print(f"fetch_messages failed for channel {cid}: {type(e).__name__}: {e}")
                            break
                    if per_channel_sleep > 0:
                        await asyncio.sleep(per_channel_sleep)

            tasks = [fetch_one(int(cid)) for cid in channel_ids]
            if tasks:
                await asyncio.gather(*tasks)
    finally:
        await rest_app.close()

    # Process oldest -> newest for stable, resumable indexing
    out.sort(key=lambda m: m.created_at)
    return out
