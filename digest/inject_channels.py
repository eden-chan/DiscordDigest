import argparse
import json
import os
from typing import Any, Dict, List, Tuple


def load_channels_source(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        arr = data
    elif isinstance(data, dict):
        arr = data.get("channels") if isinstance(data.get("channels"), list) else []
    else:
        arr = []
    out: List[Dict[str, Any]] = []
    for ch in arr:
        if not isinstance(ch, dict):
            continue
        if "id" not in ch:
            continue
        out.append({
            "id": int(ch["id"]),
            "name": ch.get("name"),
            "label": ch.get("label") or (f"#{ch.get('name')} — {int(ch['id'])}" if ch.get("name") else f"Channel {int(ch['id'])}"),
            "type": ch.get("type")
        })
    return out


def parse_ids_arg(ids_arg: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for part in ids_arg.split(","):
        s = part.strip()
        if not s:
            continue
        try:
            cid = int(s)
        except Exception:
            continue
        items.append({"id": cid, "label": f"Channel {cid}"})
    return items


def inject_channels(target_path: str, channels: List[Dict[str, Any]], nest: str | None = None) -> None:
    if not channels:
        raise SystemExit("No channels provided to inject")
    if not os.path.exists(target_path):
        raise SystemExit(f"Target JSON not found: {target_path}")
    with open(target_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise SystemExit("Target JSON must be an object (dict)")

    # Determine container to write into
    container = data
    if nest:
        # e.g., 'guild'
        if nest not in data or not isinstance(data[nest], dict):
            data[nest] = {}
        container = data[nest]

    existing = container.get("channels")
    if not isinstance(existing, list):
        existing = []
    # Deduplicate on id
    seen = {int(item.get("id")) for item in existing if isinstance(item, dict) and "id" in item}
    for ch in channels:
        cid = int(ch["id"])  # assume validated
        if cid in seen:
            continue
        # normalize label
        label = ch.get("label") or (f"#{ch.get('name')} — {cid}" if ch.get("name") else f"Channel {cid}")
        merged = {"id": cid, "label": label}
        if ch.get("name"):
            merged["name"] = ch["name"]
        if ch.get("type"):
            merged["type"] = ch["type"]
        existing.append(merged)
        seen.add(cid)
    container["channels"] = existing

    # Write back atomically
    tmp = target_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, target_path)


def main() -> None:
    p = argparse.ArgumentParser(description="Inject channels into an existing JSON without overwriting other fields")
    p.add_argument("--path", default="data/channels.json", help="Target JSON path")
    p.add_argument("--from-file", dest="from_file", help="Channels source JSON (list or {channels: [...]})")
    p.add_argument("--ids", help="Comma-separated channel IDs (e.g., 123,456)")
    p.add_argument("--nest", choices=["guild", "top"], default="top", help="Where to inject: top-level or under 'guild'")
    args = p.parse_args()

    channels: List[Dict[str, Any]] = []
    if args.from_file:
        channels = load_channels_source(args.from_file)
    elif args.ids:
        channels = parse_ids_arg(args.ids)
    else:
        raise SystemExit("Provide --from-file or --ids")

    inject_channels(args.path, channels, None if args.nest == "top" else "guild")
    print(f"Injected {len(channels)} channels into {args.path} ({'guild.channels' if args.nest=='guild' else 'channels'}).")


if __name__ == "__main__":
    main()

