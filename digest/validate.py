import argparse
import json
import os
import sys
from typing import Any, List, Tuple


def _parse_static_channels(data: Any) -> List[Tuple[int, str]]:
    """Parse (id, label) pairs from common shapes.

    Accepts any of the following:
    - {"channels": [ {"id": .., "name": .., "label": ..}, ... ]}
    - {"items": [ ... ]}
    - {"data": {"channels": [ ... ]}}
    - [ {"id": .., ...}, ... ] (top-level list)
    """
    def norm_id(v):
        try:
            return int(v)
        except Exception:
            return None

    arr = None
    if isinstance(data, list):
        arr = data
    elif isinstance(data, dict):
        for key in ("channels", "items"):
            maybe = data.get(key)
            if isinstance(maybe, list):
                arr = maybe
                break
        if arr is None and isinstance(data.get("data"), dict):
            inner = data.get("data")
            if isinstance(inner, dict) and isinstance(inner.get("channels"), list):
                arr = inner.get("channels")
        if arr is None and isinstance(data.get("guild"), dict):
            inner = data.get("guild")
            if isinstance(inner, dict) and isinstance(inner.get("channels"), list):
                arr = inner.get("channels")
    if not isinstance(arr, list):
        return []

    out: List[Tuple[int, str]] = []
    for ch in arr:
        if not isinstance(ch, dict):
            continue
        cid = ch.get("id") or ch.get("channel_id") or ch.get("channelId")
        cid = norm_id(cid)
        if cid is None:
            continue
        name = ch.get("name") or ch.get("channel_name")
        label = ch.get("label")
        if not label:
            label = f"#{name} — {cid}" if name else f"Channel {cid}"
        out.append((cid, str(label)))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate channels JSON file")
    parser.add_argument("--path", default="data/channels.json", help="Path to channels JSON")
    args = parser.parse_args()

    path = args.path
    if not os.path.exists(path):
        print(f"Channels file not found: {path}")
        sys.exit(1)

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Failed to load {path}: {e}")
        sys.exit(1)

    pairs = _parse_static_channels(data)
    if not pairs:
        keys = list(data.keys()) if isinstance(data, dict) else [type(data).__name__]
        print(f"No channels parsed. Top-level keys: {keys}")
        print("Expected shapes include: {\"channels\": [...]}, {\"data\": {\"channels\": [...]}} or a top-level list.")
        sys.exit(1)

    # Basic integrity checks
    ids = [cid for cid, _label in pairs]
    dupes = {x for x in ids if ids.count(x) > 1}
    if dupes:
        print(f"Warning: duplicate channel IDs found: {sorted(dupes)}")

    print(f"Valid channels file: {path}")
    print(f"Total channels: {len(pairs)}")
    for cid, label in pairs[:10]:
        print(f"- {label}")


if __name__ == "__main__":
    main()
