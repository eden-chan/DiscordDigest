import asyncio
import os
from datetime import datetime, timezone
from typing import Iterable, Tuple


def _ensure_data_dir() -> None:
    os.makedirs("data", exist_ok=True)


# Prevent repeated prisma generate/push within a single process
_SCHEMA_READY = False


async def _maybe_generate_client() -> None:
    """Attempt to generate Prisma client if it doesn't exist or isn't generated."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    need_generate = False
    try:
        from prisma import Prisma  # triggers getattr on missing client
        _ = Prisma
    except RuntimeError:
        # Client module present but not generated
        need_generate = True
    except Exception:
        # prisma not installed or other import error
        need_generate = True

    if not need_generate:
        return
    # Try to run `python -m prisma generate`
    try:
        import subprocess, sys, os
        print("[prisma] Generating client...")
        env = os.environ.copy()
        # Prepend venv bin to PATH if present
        vbin = os.path.abspath(os.path.join(sys.prefix, 'bin'))
        env['PATH'] = f"{vbin}:{env.get('PATH','')}"
        env['PRISMA_PY_GENERATOR'] = os.path.join(vbin, 'prisma-client-py')
        subprocess.run([os.path.join(vbin, "prisma"), "generate"], check=True, env=env)
    except Exception as e:
        print(f"[prisma] generate failed or prisma not installed: {e}")


async def _maybe_push_db() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    try:
        import subprocess, sys, os
        print("[prisma] Pushing schema (db push)...")
        env = os.environ.copy()
        vbin = os.path.abspath(os.path.join(sys.prefix, 'bin'))
        env['PATH'] = f"{vbin}:{env.get('PATH','')}"
        env['PRISMA_PY_GENERATOR'] = os.path.join(vbin, 'prisma-client-py')
        subprocess.run([os.path.join(vbin, "prisma"), "db", "push"], check=True, env=env)
    except Exception as e:
        print(f"[prisma] db push failed: {e}")
    else:
        _SCHEMA_READY = True


async def connect_client():
    await ensure_schema()
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
    rows = await client.channel.find_many(where=where)
    return [int(r.id) for r in rows]


async def list_inactive_channels(client, guild_id: int | None = None):
    where = {"isActive": False}
    if guild_id is not None:
        where = {"AND": [where, {"guildId": int(guild_id)}]}
    return await client.channel.find_many(where=where, order={"name": "asc"})


async def ensure_schema():
    await _maybe_generate_client()
    await _maybe_push_db()


# OAuth token storage
async def upsert_oauth_token(
    client,
    *,
    provider: str = "discord",
    token_type: str = "Bearer",
    access_token: str,
    refresh_token: str | None = None,
    scope: str | None = None,
    expires_at: datetime | None = None,
):
    # Prisma enum casing
    tt = "Bearer" if str(token_type).lower() == "bearer" else "Bot"
    await client.oauthtoken.upsert(
        where={"provider_tokenType": {"provider": provider, "tokenType": tt}},
        data={
            "create": {
                "provider": provider,
                "tokenType": tt,
                "accessToken": access_token,
                "refreshToken": refresh_token,
                "scope": scope,
                "expiresAt": expires_at,
            },
            "update": {
                "accessToken": access_token,
                "refreshToken": refresh_token,
                "scope": scope,
                "expiresAt": expires_at,
            },
        },
    )


async def get_oauth_token(client, *, provider: str = "discord", token_type: str = "Bearer"):
    tt = "Bearer" if str(token_type).lower() == "bearer" else "Bot"
    return await client.oauthtoken.find_unique(where={"provider_tokenType": {"provider": provider, "tokenType": tt}})


def upsert_oauth_token_sync(**kwargs) -> None:
    async def _run():
        await ensure_schema()
        client = await connect_client()
        try:
            await upsert_oauth_token(client, **kwargs)
        finally:
            await client.disconnect()

    asyncio.run(_run())


def get_oauth_token_sync(provider: str = "discord", token_type: str = "Bearer"):
    async def _run():
        await ensure_schema()
        client = await connect_client()
        try:
            return await get_oauth_token(client, provider=provider, token_type=token_type)
        finally:
            await client.disconnect()

    return asyncio.run(_run())
