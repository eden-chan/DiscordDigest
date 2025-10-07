import asyncio
from typing import Iterable

import hikari


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
