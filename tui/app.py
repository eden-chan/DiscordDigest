import asyncio
import datetime as dt
from textwrap import shorten
from typing import List
import os
import json

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Header, Footer, SelectionList, Log
from textual.widgets.selection_list import Selection

from digest.config import Config
from digest.discover import list_guild_channels
from digest.db import connect_client, list_db_channels, ensure_schema
from digest.fetch import fetch_recent_messages
from digest.scoring import select_top
from digest.summarize import summarize_with_gemini, naive_extract


class DigestTUI(App):
    BINDINGS = [
        ("r", "refresh", "Refresh channels"),
        ("d", "dry_run", "Dry run digest"),
        ("h", "hours", "Cycle lookback hours"),
        ("q", "app.quit", "Quit"),
    ]

    CSS = """
    #channels { width: 40%; }
    #output { width: 60%; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.cfg: Config | None = None
        self.hours: int = 72

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield SelectionList(id="channels")
            yield Log(id="output")
        yield Footer()

    async def on_mount(self) -> None:
        self.cfg = Config.from_env()
        self.hours = self.cfg.time_window_hours
        await self._load_channels()

    async def _load_channels(self) -> None:
        log = self.query_one(Log)
        log.write_line("Loading channels…")
        try:
            sel = self.query_one(SelectionList)
            # Clear options robustly across Textual versions
            if hasattr(sel, "clear_options"):
                try:
                    sel.clear_options()  # type: ignore[attr-defined]
                except Exception:
                    pass
            elif hasattr(sel, "clear"):
                try:
                    sel.clear()
                except Exception:
                    pass
            if not self.cfg:
                log.write_line("Config not loaded.")
                return
            # 1) Prefer SQLite DB (source of truth)
            try:
                await ensure_schema()
                client = await connect_client()
                try:
                    gid = self.cfg.guild_id if self.cfg else None
                    rows = await list_db_channels(client, gid)
                finally:
                    await client.disconnect()
                if rows:
                    resolved = 0
                    for ch in rows:
                        label = f"#{ch.name} — {ch.id}" if ch.name else f"Channel {ch.id}"
                        if ch.name:
                            resolved += 1
                        self._sel_add(sel, Selection(label, int(ch.id)))
                    log.write_line(
                        f"Loaded {len(rows)} channels from SQLite. Resolved names: {resolved}/{len(rows)}. Press 'd' to dry-run."
                    )
                    return
            except Exception as e:
                log.write_line(f"DB load failed: {e}")

            # Guidance only (no static JSON fallback)
            log.write_line("No channels in SQLite. Run `python -m digest --sync-channels` (Bot token), then press 'r'.")
            return
        except Exception as e:
            log.write_line(f"Error loading channels: {e}")

    def action_hours(self) -> None:
        # cycle through common presets
        presets = [24, 48, 72]
        try:
            idx = presets.index(self.hours)
        except ValueError:
            idx = -1
        self.hours = presets[(idx + 1) % len(presets)]
        self.query_one(Log).write_line(f"Lookback hours set to {self.hours}")

    def action_refresh(self) -> None:
        asyncio.create_task(self._load_channels())

    def action_dry_run(self) -> None:
        asyncio.create_task(self._do_dry_run())

    # Export to JSON has been removed; DB is the source of truth.

    async def _do_dry_run(self) -> None:
        log = self.query_one(Log)
        sel = self.query_one(SelectionList)
        selected = self._get_selected_ids(sel)

        if not selected:
            log.write_line("No channels selected.")
            return

        if not self.cfg:
            log.write_line("Config not loaded.")
            return

        since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=self.hours)
        log.write_line(f"Fetching recent messages (last {self.hours}h) from {len(selected)} channels…")
        try:
            msgs = await fetch_recent_messages(self.cfg.token, self.cfg.token_type, selected, since)
        except Exception as e:
            log.write_line(f"Fetch failed: {e}")
            return

        if not msgs:
            log.write_line("No recent messages found.")
            return

        top = select_top(msgs, self.cfg.top_n, now=dt.datetime.now(dt.timezone.utc), window_start=since)
        log.write_line(f"Top {len(top)} messages:")
        for m in top:
            ts = m.created_at.astimezone().strftime("%Y-%m-%d %H:%M")
            preview = shorten((m.content or "").replace("\n", " "), width=140, placeholder="…")
            log.write_line(f"- {ts} | ch={m.channel_id} | reacts={m.reactions_total} att={m.attachments}")
            if preview:
                log.write_line(f"  {preview}")
            log.write_line(f"  {m.link}")

        log.write_line("\nSummarizing…")
        if self.cfg.gemini_api_key:
            summary = await summarize_with_gemini(self.cfg.gemini_api_key, top)
        else:
            summary = naive_extract(top)
        log.write_line("== Summary ==")
        for line in summary.splitlines():
            log.write_line(line)

    # _do_export was removed.

    async def _resolve_channel_labels(self, ids: list[int]) -> list[tuple[int, str]]:
        # Import here to avoid top-level dependency during docs or tooling.
        import hikari
        labels: list[tuple[int, str]] = []
        rest_app = hikari.RESTApp()
        await rest_app.start()
        try:
            if not self.cfg:
                return []
            async with rest_app.acquire(self.cfg.token, token_type=self.cfg.token_type) as rest:
                for cid in ids:
                    label = f"Channel {cid}"
                    try:
                        ch = await rest.fetch_channel(cid)
                        name = getattr(ch, "name", None)
                        if name:
                            label = f"#{name} — {cid}"
                    except Exception:
                        # Keep fallback label
                        pass
                    labels.append((cid, label))
        finally:
            await rest_app.close()
        return labels

    @staticmethod
    def _sel_add(sel: SelectionList, opt: Selection) -> None:  # type: ignore[name-defined]
        if hasattr(sel, "add_option"):
            try:
                sel.add_option(opt)  # type: ignore[attr-defined]
                return
            except Exception:
                pass
        if hasattr(sel, "append"):
            try:
                sel.append(opt)  # type: ignore[attr-defined]
                return
            except Exception:
                pass
        try:
            options = list(getattr(sel, "options", []))
            options.append(opt)
            setattr(sel, "options", options)
        except Exception:
            raise

    @staticmethod
    def _parse_static_channels(data: object) -> list[tuple[int, str, bool]]:
        """Parse various common shapes of static channel exports.

        Returns list of (id, label, has_name).
        Accepted shapes:
        - {"channels": [ {"id": .., "name": .., "label": ..}, ... ]}
        - [ {"id": .., ...}, ... ] (top-level list)
        - {"data": {"channels": [...]}}
        - {"guild": {"channels": [...]}}
        - {"items": [...]}
        """
        def norm_id(v) -> int | None:
            try:
                return int(v)
            except Exception:
                return None

        # Decide where the array lives
        arr = None
        if isinstance(data, list):
            arr = data
        elif isinstance(data, dict):
            for key in ("channels", "items"):
                if isinstance(data.get(key), list):
                    arr = data.get(key)
                    break
            if arr is None and isinstance(data.get("data"), dict):
                inner = data.get("data")
                if isinstance(inner.get("channels"), list):
                    arr = inner.get("channels")
            if arr is None and isinstance(data.get("guild"), dict):
                g = data.get("guild")
                if isinstance(g.get("channels"), list):
                    arr = g.get("channels")
        if not isinstance(arr, list):
            return []

        out: list[tuple[int, str, bool]] = []
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
            out.append((cid, str(label), bool(name)))
        return out

    @staticmethod
    def _get_selected_ids(sel: SelectionList) -> List[int]:  # type: ignore[name-defined]
        """Extract selected option IDs from SelectionList across Textual versions."""
        # Newer versions: 'selected' iterable of Selection objects
        try:
            seq = getattr(sel, "selected", None)
            if seq is not None:
                vals: List[int] = []
                for item in list(seq):
                    v = getattr(item, "value", None)
                    if v is None:
                        v = getattr(item, "id", None)
                    if v is None:
                        try:
                            v = item[1]
                        except Exception:
                            v = None
                    if v is not None:
                        try:
                            vals.append(int(v))
                        except Exception:
                            pass
                if vals:
                    return vals
        except Exception:
            pass
        # Fallback methods
        for name in ("get_selected_values", "get_selected"):
            func = getattr(sel, name, None)
            if callable(func):
                try:
                    res = func()
                    vals: List[int] = []
                    for item in list(res):
                        if isinstance(item, tuple) and len(item) >= 2:
                            v = item[1]
                        else:
                            v = getattr(item, "value", getattr(item, "id", None))
                        if v is not None:
                            try:
                                vals.append(int(v))
                            except Exception:
                                pass
                    if vals:
                        return vals
                except Exception:
                    continue
        return []
