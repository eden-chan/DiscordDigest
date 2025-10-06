import os
import json
from dataclasses import dataclass
from typing import List, Optional

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Config:
    token: str
    token_type: str
    digest_channel_id: int
    include_channel_ids: List[int]
    time_window_hours: int = 72
    top_n: int = 5
    gemini_api_key: Optional[str] = None
    guild_id: Optional[int] = None

    @staticmethod
    def from_env() -> "Config":
        token = (
            os.getenv("TOKEN")
            or os.getenv("DISCORD_TOKEN")
            or os.getenv("OAUTH_ACCESS_TOKEN")
        )
        token_type_val = os.getenv("DISCORD_TOKEN_TYPE", "Bot")

        if not token:
            # Try cached OAuth token JSON
            path = os.getenv("OAUTH_TOKEN_PATH", os.path.join("data", "oauth_token.json"))
            try:
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        tok = json.load(f)
                    token = tok.get("access_token")
                    tt = tok.get("token_type")
                    if tt:
                        token_type_val = str(tt)
            except Exception:
                token = None
        if not token:
            # Fallback: read top-level access_token/token_type from data/channels.json
            try:
                ch_path = os.path.join("data", "channels.json")
                if os.path.exists(ch_path):
                    with open(ch_path, "r", encoding="utf-8") as f:
                        dat = json.load(f)
                    if isinstance(dat, dict):
                        token = dat.get("access_token") or token
                        tt = dat.get("token_type")
                        if tt:
                            token_type_val = str(tt)
            except Exception:
                pass
        if not token:
            raise RuntimeError("Missing TOKEN/DISCORD_TOKEN/OAUTH token in env or cache")

        digest_channel = os.getenv("DIGEST_CHANNEL_ID")
        if not digest_channel:
            raise RuntimeError("Missing DIGEST_CHANNEL_ID in environment")

        raw_include = os.getenv("INCLUDE_CHANNEL_IDS", "").strip()
        include_ids: List[int] = []
        if raw_include:
            for part in raw_include.split(","):
                part = part.strip()
                if part:
                    include_ids.append(int(part))

        guild_id = os.getenv("GUILD_ID")

        return Config(
            token=token,
            token_type=token_type_val,
            digest_channel_id=int(digest_channel),
            include_channel_ids=include_ids,
            time_window_hours=int(os.getenv("TIME_WINDOW_HOURS", "72")),
            top_n=int(os.getenv("TOP_N_CONVOS", "5")),
            gemini_api_key=os.getenv("GEMINI_API_KEY"),
            guild_id=int(guild_id) if guild_id else None,
        )
