import asyncio
import re
from textwrap import shorten
from typing import Iterable, Optional

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
