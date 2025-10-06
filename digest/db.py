import asyncio
import os
from datetime import datetime, timezone
from typing import Iterable, Tuple


def _ensure_data_dir() -> None:
    os.makedirs("data", exist_ok=True)


async def _maybe_generate_client() -> None:
    """Attempt to generate Prisma client if it doesn't exist."""
    try:
        from prisma import Prisma  # noqa: F401
        return
    except Exception:
        pass
    # Try to run `python -m prisma generate`
    try:
        import subprocess, sys

        subprocess.run([sys.executable, "-m", "prisma", "generate"], check=False)
    except Exception:
        return


async def _maybe_push_db() -> None:
    try:
        import subprocess, sys

        subprocess.run([sys.executable, "-m", "prisma", "db", "push"], check=False)
    except Exception:
        return


async def connect_client():
    await _maybe_generate_client()
    from prisma import Prisma

    client = Prisma()
    await client.connect()
    return client


def _map_channel_type(raw: str) -> str:
    v = (raw or "").upper()
    # Normalize common variants
    alias = {
        "TEXT_CHANNEL": "GUILD_TEXT",
        "NEWS": "GUILD_NEWS",
        "ANNOUNCEMENT": "GUILD_NEWS",
        "STAGE_CHANNEL": "GUILD_STAGE_VOICE",
        "PUBLIC_THREAD": "GUILD_PUBLIC_THREAD",
        "PRIVATE_THREAD": "GUILD_PRIVATE_THREAD",
    }
    return alias.get(v, v if v else "GUILD_TEXT")


async def upsert_guild(client, guild_id: int, name: str | None = None, icon: str | None = None):
    await client.guild.upsert(
        where={"id": guild_id},
        data={
            "create": {"id": guild_id, "name": name, "icon": icon},
            "update": {"name": name, "icon": icon},
        },
    )


async def upsert_channels(
    client,
    guild_id: int,
    items: Iterable[Tuple[int, str | None, str | None, int | None, int | None, bool | None, int | None, int | None]],
):
    """Upsert channels for a guild.

    items tuple: (id, name, type, parent_id, position, nsfw, rate_limit_per_user, seen_flag)
    Only id/name/type are typically provided; others may be None.
    """
    now = datetime.now(timezone.utc)
    seen_ids: set[int] = set()
    for cid, name, ctype, parent_id, position, nsfw, rate_limit, _seen in items:
        seen_ids.add(int(cid))
        await client.channel.upsert(
            where={"id": int(cid)},
            data={
                "create": {
                    "id": int(cid),
                    "guildId": int(guild_id),
                    "name": name,
                    "type": _map_channel_type(ctype or "GUILD_TEXT"),
                    "parentId": int(parent_id) if parent_id else None,
                    "position": position,
                    "nsfw": nsfw,
                    "rateLimitPerUser": rate_limit,
                    "lastSyncedAt": now,
                },
                "update": {
                    "name": name,
                    "type": _map_channel_type(ctype or "GUILD_TEXT"),
                    "parentId": int(parent_id) if parent_id else None,
                    "position": position,
                    "nsfw": nsfw,
                    "rateLimitPerUser": rate_limit,
                    "isActive": True,
                    "lastSyncedAt": now,
                },
            },
        )

    # Deactivate channels not seen this run
    await client.channel.update_many(
        where={"guildId": int(guild_id), "id": {"notIn": list(seen_ids)}},
        data={"isActive": False},
    )


async def list_db_channels(client, guild_id: int | None = None):
    if guild_id is not None:
        return await client.channel.find_many(where={"guildId": int(guild_id)}, order={"name": "asc"})
    return await client.channel.find_many(order={"name": "asc"})


async def list_active_channel_ids(client, guild_id: int | None = None) -> list[int]:
    where = {"isActive": True}
    if guild_id is not None:
        where = {"AND": [where, {"guildId": int(guild_id)}]}
    rows = await client.channel.find_many(where=where, select={"id": True})
    return [int(r.id) for r in rows]


async def ensure_schema():
    await _maybe_generate_client()
    await _maybe_push_db()
