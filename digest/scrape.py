import argparse
import asyncio
import datetime as dt
import json
import os
from typing import List, Tuple

from .config import Config
from .discover import list_guild_channels


async def _resolve_labels(token: str, token_type: str, ids: List[int]) -> List[Tuple[int, str, str]]:
    """Resolve channel name and type for given IDs. Falls back when not permitted.

    Returns a list of tuples: (id, name_or_empty, type_or_empty)
    """
    try:
        import hikari
    except Exception:
        return [(int(cid), "", "") for cid in ids]

    rest_app = hikari.RESTApp()
    await rest_app.start()
    out: List[Tuple[int, str, str]] = []
    try:
        async with rest_app.acquire(token, token_type=token_type) as rest:
            for cid in ids:
                name = ""
                ctype = ""
                try:
                    ch = await rest.fetch_channel(cid)
                    n = getattr(ch, "name", None)
                    if n:
                        name = str(n)
                    t = getattr(ch, "type", None)
                    if t is not None and hasattr(t, "name"):
                        ctype = str(t.name)
                    elif ch is not None:
                        ctype = ch.__class__.__name__
                except Exception:
                    pass
                out.append((int(cid), name, ctype))
    finally:
        await rest_app.close()
    return out


async def scrape_channels(out_path: str) -> None:
    cfg = Config.from_env()

    rows: List[Tuple[int, str, str]] = []
    mode = ""

    if cfg.token_type.lower() == "bot" and cfg.guild_id:
        # Full guild listing
        items = await list_guild_channels(cfg.token, cfg.token_type, cfg.guild_id)
        rows = [(cid, name or "", ctype or "") for cid, name, ctype in items]
        mode = "guild_list"
    else:
        # Fallback: resolve only configured IDs
        ids = cfg.include_channel_ids
        label_rows = await _resolve_labels(cfg.token, cfg.token_type, ids)
        rows = label_rows
        mode = "include_ids"

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    data = {
        "guild_id": cfg.guild_id,
        "scrape_mode": mode,
        "fetched_at": now,
        "channels": [
            {
                "id": cid,
                "name": name or None,
                "type": ctype or None,
                "label": f"#{name} — {cid}" if name else f"Channel {cid}",
            }
            for cid, name, ctype in rows
        ],
    }

    if out_path:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape Discord channels to JSON")
    parser.add_argument("--out", default="data/channels.json", help="Output JSON path")
    args = parser.parse_args()
    asyncio.run(scrape_channels(args.out))


if __name__ == "__main__":
    main()

