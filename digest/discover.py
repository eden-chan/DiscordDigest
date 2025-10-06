from typing import List, Tuple

import hikari


async def list_guild_channels(
    token: str, token_type: str, guild_id: int
) -> List[Tuple[int, str, str]]:
    rest_app = hikari.RESTApp()
    await rest_app.start()
    try:
        async with rest_app.acquire(token, token_type=token_type) as rest:
            channels = await rest.fetch_guild_channels(guild_id)
            out: List[Tuple[int, str, str]] = []
            for ch in channels:
                name = getattr(ch, "name", None) or str(ch.id)
                ctype = ch.type.name if hasattr(ch, "type") and hasattr(ch.type, "name") else ch.__class__.__name__
                out.append((int(ch.id), name, ctype))
            # Stable order by type then name then id
            out.sort(key=lambda x: (x[2], x[1].lower(), x[0]))
            return out
    finally:
        await rest_app.close()
