import asyncio
import datetime as dt
from dataclasses import dataclass
from typing import Iterable, List, Sequence

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
    reactions_total: int = 0
    attachments: int = 0


async def fetch_recent_messages(
    token: str,
    token_type: str,
    channel_ids: Iterable[int],
    since: dt.datetime,
    limit_per_channel: int = 200,
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
            sem = asyncio.Semaphore(5)

            async def fetch_one(cid: int) -> None:
                async with sem:
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
                            try:
                                attachments = len(m.attachments) if m.attachments else 0
                            except Exception:
                                attachments = 0

                            out.append(
                                SimpleMessage(
                                    id=int(m.id),
                                    channel_id=int(m.channel_id),
                                    author_id=int(m.author.id) if m.author else 0,
                                    created_at=ts,
                                    content=content,
                                    link=msg_link,
                                    reactions_total=total_reacts,
                                    attachments=attachments,
                                )
                            )
                    except Exception as e:
                        if os.getenv("DIGEST_DEBUG"):
                            print(f"fetch_messages failed for channel {cid}: {type(e).__name__}: {e}")
                        return

            tasks = [fetch_one(int(cid)) for cid in channel_ids]
            if tasks:
                await asyncio.gather(*tasks)
    finally:
        await rest_app.close()

    # Sort newest first for consistency
    out.sort(key=lambda m: m.created_at, reverse=True)
    return out
