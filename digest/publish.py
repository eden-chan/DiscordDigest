import asyncio
from typing import Iterable

import hikari
import httpx
from typing import Optional


async def post_text(
    token: str,
    channel_id: int,
    lines: Iterable[str],
    block_size: int = 1800,
    *,
    token_type: str = "Bot",
) -> None:
    rest_app = hikari.RESTApp()
    await rest_app.start()
    try:
        async with rest_app.acquire(token, token_type=token_type) as rest:
            buf = ""
            for line in lines:
                if len(buf) + len(line) + 1 > block_size:
                    await rest.create_message(channel_id, buf)
                    await asyncio.sleep(0)
                    buf = ""
                buf += ("\n" if buf else "") + line
            if buf:
                await rest.create_message(channel_id, buf)
    finally:
        await rest_app.close()


async def post_one(
    token: str,
    channel_id: int,
    content: str,
    *,
    token_type: str = "Bot",
) -> None:
    """Post exactly one message to a channel.

    Caller must ensure `content` is below Discord limits (~2000 chars).
    """
    rest_app = hikari.RESTApp()
    await rest_app.start()
    try:
        async with rest_app.acquire(token, token_type=token_type) as rest:
            await rest.create_message(channel_id, content)
    finally:
        await rest_app.close()


def _thread_type_to_int(t: str | int | None) -> Optional[int]:
    if t is None:
        return None
    if isinstance(t, int):
        return t
    v = str(t).upper()
    mapping = {
        "GUILD_PUBLIC_THREAD": 11,
        "GUILD_PRIVATE_THREAD": 12,
        "GUILD_NEWS_THREAD": 10,
        "PUBLIC": 11,
        "PRIVATE": 12,
    }
    return mapping.get(v)


async def create_thread(
    token: str,
    channel_id: int,
    *,
    name: str,
    token_type: str = "Bot",
    auto_archive_duration: int = 10080,
    thread_type: str | int | None = "GUILD_PUBLIC_THREAD",
    rate_limit_per_user: int | None = None,
    invitable: bool | None = None,
) -> Optional[int]:
    """Create a thread in a text/announcement channel. Returns thread_id or None.

    Uses Discord REST v10: POST /channels/{channel_id}/threads
    """
    # Hikari REST may not expose a direct helper for this; use httpx for now
    headers = {
        "Authorization": f"{token_type} {token}",
        "User-Agent": "DiscordDigest/1.0",
        "Content-Type": "application/json",
    }
    url = f"https://discord.com/api/v10/channels/{int(channel_id)}/threads"
    payload: dict = {
        "name": name,
        "auto_archive_duration": int(auto_archive_duration),
    }
    tt = _thread_type_to_int(thread_type)
    if tt is not None:
        payload["type"] = int(tt)
    if rate_limit_per_user is not None:
        payload["rate_limit_per_user"] = int(rate_limit_per_user)
    if invitable is not None:
        payload["invitable"] = bool(invitable)
    try:
        async with httpx.AsyncClient(timeout=20) as hc:
            r = await hc.post(url, headers=headers, json=payload)
        if r.status_code in (200, 201):
            data = r.json()
            return int(data.get("id")) if data.get("id") else None
        else:
            # Silently fail for now; caller may fallback
            return None
    except Exception:
        return None
