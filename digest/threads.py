import asyncio
import datetime as dt
from typing import Iterable, List, Optional

import hikari
import httpx

from .db import ensure_schema, connect_client


async def _upsert_thread_channel(client, *, thread: hikari.GuildThreadChannel) -> None:
    """Upsert a thread as a Channel row with parentId set.

    Stores minimal fields: id, guildId, name, type, parentId, isActive, lastSyncedAt.
    """
    ttype = getattr(thread.type, "name", None) or thread.__class__.__name__
    name = getattr(thread, "name", None)
    guild_id = int(getattr(thread, "guild_id", 0)) if getattr(thread, "guild_id", None) else None
    parent_id = int(getattr(thread, "parent_id", 0)) if getattr(thread, "parent_id", None) else None

    await client.channel.upsert(
        where={"id": int(thread.id)},
        data={
            "create": {
                "id": int(thread.id),
                "guildId": int(guild_id) if guild_id else 0,
                "name": name,
                "type": str(ttype),
                "parentId": int(parent_id) if parent_id else None,
                "isActive": True,
                "lastSyncedAt": dt.datetime.now(dt.timezone.utc),
            },
            "update": {
                "name": name,
                "type": str(ttype),
                "parentId": int(parent_id) if parent_id else None,
                "isActive": True,
                "lastSyncedAt": dt.datetime.now(dt.timezone.utc),
            },
        },
    )


async def sync_threads(
    token: str,
    token_type: str,
    guild_id: int,
    *,
    parents: Optional[List[int]] = None,
    verbose: bool = False,
) -> int:
    """Discover and upsert thread channels for a guild.

    - Upserts active guild threads.
    - If `parents` provided, attempts to fetch archived public threads for each parent; if the method isn't available, logs and continues.
    Returns the number of thread channels upserted.
    """
    await ensure_schema()
    client = await connect_client()
    count = 0
    # Try Hikari helpers first; otherwise fall back to raw HTTP.
    rest_app = hikari.RESTApp()
    await rest_app.start()
    try:
        async with rest_app.acquire(token, token_type=token_type) as rest:
            try:
                active = await rest.fetch_active_guild_threads(guild_id)
                threads = []
                if hasattr(active, "threads"):
                    threads = list(getattr(active, "threads"))
                elif isinstance(active, list):
                    threads = list(active)
                for th in threads:
                    try:
                        await _upsert_thread_channel(client, thread=th)
                        count += 1
                        if verbose:
                            print(f"[threads] upsert active: {getattr(th,'name',th.id)} ({int(th.id)}) parent={getattr(th,'parent_id',None)}")
                    except Exception:
                        continue
            except Exception:
                if verbose:
                    print("[threads] fetch_active_guild_threads not available or failed.")
    finally:
        await rest_app.close()

    # Raw HTTP fallback for active threads
    if count == 0:
        try:
            headers = {"Authorization": f"{token_type} {token}", "User-Agent": "DiscordDigest/1.0"}
            async with httpx.AsyncClient(base_url="https://discord.com/api/v10", headers=headers, timeout=20) as hc:
                r = await hc.get(f"/guilds/{guild_id}/threads/active")
                if r.status_code == 200:
                    data = r.json()
                    threads = data.get("threads", [])
                    for raw in threads:
                        try:
                            # Build a minimal hikari-like object for upsert
                            th = type("_T", (), {})()
                            th.id = int(raw["id"]) if "id" in raw else None
                            th.name = raw.get("name")
                            th.parent_id = int(raw["parent_id"]) if raw.get("parent_id") else None
                            th.guild_id = int(raw["guild_id"]) if raw.get("guild_id") else guild_id
                            # Emulate type enum name
                            tname = raw.get("type")
                            # Discord API returns integer; map few knowns if needed
                            # We store the int or string as-is
                            th.type = type("_E", (), {"name": str(tname)})()
                            await _upsert_thread_channel(client, thread=th)
                            count += 1
                            if verbose:
                                print(f"[threads] upsert active(http): {th.name or th.id} ({int(th.id)}) parent={th.parent_id}")
                        except Exception:
                            continue
        except Exception:
            if verbose:
                print("[threads] raw HTTP active threads fetch failed.")

    # Raw HTTP archived per parent
    if parents: 
        try:
            headers = {"Authorization": f"{token_type} {token}", "User-Agent": "DiscordDigest/1.0"}
            async with httpx.AsyncClient(base_url="https://discord.com/api/v10", headers=headers, timeout=20) as hc:
                for pid in parents:
                    before = None
                    while True:
                        params = {"before": before} if before else {}
                        r = await hc.get(f"/channels/{pid}/threads/archived/public", params=params)
                        if r.status_code != 200:
                            if verbose:
                                print(f"[threads] archived(http) failed parent {pid} code={r.status_code}")
                            break
                        data = r.json()
                        threads = data.get("threads", [])
                        if not threads:
                            break
                        oldest_ts = None
                        for raw in threads:
                            try:
                                th = type("_T", (), {})()
                                th.id = int(raw["id"]) if "id" in raw else None
                                th.name = raw.get("name")
                                th.parent_id = int(raw["parent_id"]) if raw.get("parent_id") else None
                                th.guild_id = int(raw["guild_id"]) if raw.get("guild_id") else guild_id
                                tname = raw.get("type")
                                th.type = type("_E", (), {"name": str(tname)})()
                                await _upsert_thread_channel(client, thread=th)
                                count += 1
                                if verbose:
                                    print(f"[threads] upsert archived(http): {th.name or th.id} ({int(th.id)}) parent={th.parent_id}")
                                meta = raw.get("thread_metadata") or {}
                                ats = meta.get("archive_timestamp")
                                if ats:
                                    try:
                                        ts = dt.datetime.fromisoformat(ats.replace("Z", "+00:00"))
                                        if oldest_ts is None or ts < oldest_ts:
                                            oldest_ts = ts
                                    except Exception:
                                        pass
                            except Exception:
                                continue
                        if not data.get("has_more"):
                            break
                        # Set before to the oldest archive timestamp to continue paging
                        if oldest_ts is not None:
                            before = oldest_ts.isoformat()
                        else:
                            break
        except Exception:
            if verbose:
                print("[threads] raw HTTP archived threads fetch failed.")

    await client.disconnect()
    return count
