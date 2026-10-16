"""
Telegram enforces a 4096-character limit per message. Long content (a full
raw ad, a big job list) must be chunked before sending, or the send call
fails outright. This module is pure string logic — no telegram dependency —
so it's testable offline.
"""
from __future__ import annotations

from typing import List

TELEGRAM_MAX_MESSAGE_LENGTH = 4096
SAFE_CHUNK_SIZE = 3500  # leave headroom for any prefix/suffix the caller adds


def chunk_text(text: str, chunk_size: int = SAFE_CHUNK_SIZE) -> List[str]:
    """
    Splits text into chunks no larger than chunk_size, preferring to break on
    paragraph or line boundaries rather than mid-word/mid-line where possible.
    """
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    remaining = text
    while len(remaining) > chunk_size:
        window = remaining[:chunk_size]
        split_at = window.rfind("\n\n")
        if split_at < chunk_size * 0.5:
            split_at = window.rfind("\n")
        if split_at < chunk_size * 0.5:
            split_at = window.rfind(" ")
        if split_at <= 0:
            split_at = chunk_size

        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    if remaining:
        chunks.append(remaining)

    return chunks


async def send_long_text(reply_fn, text: str, chunk_size: int = SAFE_CHUNK_SIZE, **kwargs):
    """
    reply_fn: an async callable like `message.reply_text` — called once per
    chunk. Any extra kwargs (e.g. reply_markup) are attached to the LAST
    chunk only, since that's usually where inline buttons belong.
    """
    chunks = chunk_text(text, chunk_size)
    for i, chunk in enumerate(chunks):
        is_last = i == len(chunks) - 1
        if is_last and kwargs:
            await reply_fn(chunk, **kwargs)
        else:
            await reply_fn(chunk)
