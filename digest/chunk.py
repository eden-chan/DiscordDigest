from typing import Iterable, List


def _split_long_line(line: str, max_chars: int) -> List[str]:
    """Split a long line into pieces not exceeding max_chars.

    Does not modify content (no truncation or ellipsis); purely splits.
    """
    if max_chars <= 0:
        return [line]
    out: List[str] = []
    s = line
    while s:
        out.append(s[:max_chars])
        s = s[max_chars:]
    return out


def chunk_lines(lines: Iterable[str], max_chars: int = 1800) -> List[str]:
    """Greedily pack lines into message-sized blocks.

    - Preserves line order and content.
    - Ensures each returned block <= max_chars.
    - Splits individual long lines if they exceed max_chars.
    """
    blocks: List[str] = []
    buf = ""
    for raw in lines:
        # Normalize to string
        line = str(raw) if raw is not None else ""
        # Break very long lines up-front
        for piece in _split_long_line(line, max_chars=max_chars):
            # Determine space needed including newline if buffer not empty
            overhead = 1 if buf else 0
            need = len(piece) + overhead
            if need > max_chars:
                # piece itself should never exceed max_chars due to split; safeguard
                for sub in _split_long_line(piece, max_chars=max_chars):
                    if buf:
                        blocks.append(buf)
                        buf = sub
                    else:
                        buf = sub
                continue
            if len(buf) + need > max_chars:
                # flush and start new buffer
                blocks.append(buf)
                buf = piece
            else:
                buf = (buf + "\n" + piece) if buf else piece
    if buf:
        blocks.append(buf)
    return blocks


def chunk_text(text: str, max_chars: int = 1800) -> List[str]:
    """Split a large text into message-sized blocks by newline, preserving content."""
    return chunk_lines(text.splitlines(), max_chars=max_chars)

