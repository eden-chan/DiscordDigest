import asyncio
import re
from textwrap import shorten
from typing import Iterable, Optional, List

from .fetch import SimpleMessage


SYSTEM_PROMPT = (
    "You are a concise summarizer for Discord conversations. "
    "Given recent snippets across channels, produce a clear digest with: \n"
    "- 3-6 bullets of key topics and decisions\n"
    "- Keep it neutral and helpful\n"
    "- Include notable links inline\n"
    "- Max 1200 characters total\n"
)


def _prepare_corpus(messages: Iterable[SimpleMessage], max_chars: int = 8000) -> str:
    lines = []
    for m in messages:
        content = re.sub(r"\s+", " ", (m.content or "").strip())
        if not content:
            continue
        lines.append(f"- {content} \n{m.link}")
        if sum(len(x) for x in lines) > max_chars:
            break
    return "\n".join(lines) if lines else "(No substantive messages found in the window.)"


async def summarize_with_gemini(
    api_key: str,
    messages: Iterable[SimpleMessage],
) -> str:
    try:
        import google.generativeai as genai
    except Exception:
        return "Gemini SDK not installed. Run: pip install google-generativeai"

    corpus = _prepare_corpus(messages)

    def _run_sync() -> str:
        genai.configure(api_key=api_key)
        prompt = f"{SYSTEM_PROMPT}\n\nRecent snippets:\n{corpus}"
        # Try common model aliases; stop at first that works
        for name in (
            "gemini-1.5-flash-latest",
            "gemini-1.5-flash",
            "gemini-1.0-pro-latest",
            "gemini-pro",
        ):
            try:
                model = genai.GenerativeModel(name)
                resp = model.generate_content(prompt)
                text = getattr(resp, "text", None) or ""
                text = text.strip()
                if text:
                    return text
            except Exception:
                continue
        return "(No summary returned.)"

    loop = asyncio.get_running_loop()
    out: str = await loop.run_in_executor(None, _run_sync)
    # Discord content limit safety
    return shorten(out, width=1800, placeholder="…")


async def summarize_with_gemini_citations(
    api_key: str,
    messages: Iterable[SimpleMessage],
    *,
    max_bullets: int = 5,
    max_chars: int = 800,
) -> List[str]:
    """Generate concise bullets that reference [n] citations based on message order.

    Returns a list of bullet lines (e.g., "- Shipped X and planned Y [1]").
    Does not include the "Citations:" section; compose that separately.
    """
    try:
        import google.generativeai as genai
    except Exception:
        # Fallback: naive short bullets
        outs: List[str] = []
        count = 0
        for m in messages:
            if not m.content:
                continue
            snippet = shorten(m.content.strip().replace("\n", " "), 100)
            count += 1
            outs.append(f"- {snippet} [{count}]")
            if count >= max_bullets:
                break
        return outs

    # Prepare an indexed corpus to enforce [n] mapping
    indexed: List[SimpleMessage] = [m for m in messages]
    # Only keep first max_bullets for mapping; LLM should reference [1..N]
    indexed = indexed[: max(1, max_bullets)]
    corpus_lines: List[str] = []
    for i, m in enumerate(indexed, start=1):
        content = (m.content or "").strip().replace("\n", " ")
        if not content:
            content = m.link or ""
        corpus_lines.append(f"[{i}] {content}")
    corpus = "\n".join(corpus_lines) if corpus_lines else "(No content)"

    prompt = (
        "You are a staff-level editor. Draft a compact, readable summary"\
        " using one-line bullets that reference the numbered sources.\n"\
        "Rules:\n"\
        f"- Use at most {max_bullets} bullets.\n"\
        "- Each bullet ≤ 16 words.\n"\
        "- End each bullet with a bracketed citation like [1] or [2].\n"\
        "- No intro or outro text; only bullets.\n"\
        "- Avoid redundancy; prefer decisions, outcomes, next steps.\n"\
        "Sources (map):\n" + corpus
    )

    def _run_sync() -> str:
        genai.configure(api_key=api_key)
        model_name = (
            os.getenv("GEMINI_MODEL")
            or "gemini-1.5-flash-latest"
        )
        try:
            model = genai.GenerativeModel(model_name)
            resp = model.generate_content(prompt)
            text = getattr(resp, "text", "").strip()
            return text
        except Exception:
            return ""

    import os
    loop = asyncio.get_running_loop()
    out: str = await loop.run_in_executor(None, _run_sync)
    if not out:
        # fallback to naive bullets
        outs: List[str] = []
        count = 0
        for m in indexed:
            if not m.content:
                continue
            snippet = shorten(m.content.strip().replace("\n", " "), 100)
            count += 1
            outs.append(f"- {snippet} [{count}]")
            if count >= max_bullets:
                break
        return outs
    # Keep within a tight budget and split into lines
    out = shorten(out, width=max_chars, placeholder="…")
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    # Ensure bullets are prefixed correctly
    norm: List[str] = []
    for ln in lines:
        s = ln
        if not s.startswith("- "):
            s = "- " + s.lstrip("- ")
        norm.append(s)
    # Cap to max_bullets
    return norm[: max_bullets]


def naive_extract(messages: Iterable[SimpleMessage]) -> str:
    # Extremely simple fallback: take first few items and list links
    lines = []
    count = 0
    for m in messages:
        if not m.content:
            continue
        snippet = shorten(m.content.strip().replace("\n", " "), 140)
        lines.append(f"- {snippet} ({m.link})")
        count += 1
        if count >= 5:
            break
    return "\n".join(lines) if lines else "No notable messages detected in the window."
