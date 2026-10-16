import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.telegram_utils import TELEGRAM_MAX_MESSAGE_LENGTH, chunk_text, send_long_text  # noqa: E402


class TestChunkText(unittest.TestCase):
    def test_short_text_returns_single_chunk(self):
        self.assertEqual(chunk_text("hello"), ["hello"])

    def test_long_text_is_split(self):
        text = "word " * 2000  # ~10000 chars
        chunks = chunk_text(text, chunk_size=3500)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 3500)

    def test_no_chunk_exceeds_telegram_limit(self):
        text = "x" * 20000
        chunks = chunk_text(text)
        for c in chunks:
            self.assertLess(len(c), TELEGRAM_MAX_MESSAGE_LENGTH)

    def test_reassembly_preserves_content_words(self):
        text = "\n\n".join(f"paragraph number {i} with some words here" for i in range(200))
        chunks = chunk_text(text, chunk_size=500)
        rejoined = "".join(chunks)
        # whitespace normalization differs slightly at split points, but no
        # words should be lost:
        for i in range(200):
            self.assertIn(f"paragraph number {i}", rejoined)


class TestSendLongText(unittest.IsolatedAsyncioTestCase):
    async def test_sends_one_call_for_short_text(self):
        calls = []

        async def fake_reply(text, **kwargs):
            calls.append((text, kwargs))

        await send_long_text(fake_reply, "short message")
        self.assertEqual(len(calls), 1)

    async def test_sends_multiple_calls_for_long_text_with_markup_on_last(self):
        calls = []

        async def fake_reply(text, **kwargs):
            calls.append((text, kwargs))

        long_text = "word " * 2000
        await send_long_text(fake_reply, long_text, chunk_size=3500, reply_markup="KEYBOARD")
        self.assertGreater(len(calls), 1)
        # Only the last call should carry the reply_markup kwarg:
        for text, kwargs in calls[:-1]:
            self.assertEqual(kwargs, {})
        self.assertEqual(calls[-1][1].get("reply_markup"), "KEYBOARD")


if __name__ == "__main__":
    unittest.main()
