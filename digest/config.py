import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Config:
    token: str
    token_type: str
    digest_channel_id: int
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
            # Fallback: read from SQLite OAuth token store
            try:
                from .db import get_oauth_token_sync
                rec = get_oauth_token_sync(provider="discord", token_type="Bearer")
                if rec is not None:
                    token = getattr(rec, "accessToken", None)
                    tt = getattr(rec, "tokenType", None)
                    if tt:
                        token_type_val = str(tt)
            except Exception:
                pass
        if not token:
            raise RuntimeError("Missing TOKEN/DISCORD_TOKEN/OAUTH token in env or cache")

        digest_channel = os.getenv("DIGEST_CHANNEL_ID")
        if not digest_channel:
            raise RuntimeError("Missing DIGEST_CHANNEL_ID in environment")

        guild_id = os.getenv("GUILD_ID")

        return Config(
            token=token,
            token_type=token_type_val,
            digest_channel_id=int(digest_channel),
            time_window_hours=int(os.getenv("TIME_WINDOW_HOURS", "72")),
            top_n=int(os.getenv("TOP_N_CONVOS", "5")),
            gemini_api_key=os.getenv("GEMINI_API_KEY"),
            guild_id=int(guild_id) if guild_id else None,
        )
