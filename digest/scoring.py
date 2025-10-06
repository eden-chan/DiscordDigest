import datetime as dt
import math
import re
from typing import Iterable, List, Tuple

from .fetch import SimpleMessage


def _has_link(text: str) -> bool:
    return bool(re.search(r"https?://", text or ""))


def score_message(m: SimpleMessage, now: dt.datetime, window_start: dt.datetime) -> float:
    s = 0.0
    # Reactions (0..1) ~ up to 5 reactions normalized
    s += min(m.reactions_total / 5.0, 1.0) * 0.45
    # Content richness (0..1) ~ 180 chars
    length = len((m.content or "").strip())
    s += min(length / 180.0, 1.0) * 0.25
    # Links and attachments
    s += (0.15 if _has_link(m.content) else 0.0)
    s += (0.05 if m.attachments > 0 else 0.0)
    # Recency boost (0..1) based on time within window
    span = max((now - window_start).total_seconds(), 1.0)
    age = (now - m.created_at).total_seconds()
    recency = max(0.0, 1.0 - age / span)
    s += recency * 0.10
    return s


def select_top(
    messages: Iterable[SimpleMessage],
    top_n: int,
    now: dt.datetime,
    window_start: dt.datetime,
) -> List[SimpleMessage]:
    scored: List[Tuple[float, SimpleMessage]] = []
    for m in messages:
        scored.append((score_message(m, now, window_start), m))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = [m for _, m in scored[: max(0, top_n)]]
    # Stability: keep newest-first order within same score selection
    out.sort(key=lambda x: x.created_at, reverse=True)
    return out

