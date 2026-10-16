import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.notification_queue import deliver_with_rate_limit  # noqa: E402


class FakeRetryAfter(Exception):
    def __init__(self, retry_after):
        self.retry_after = retry_after


class TestNotificationQueue(unittest.IsolatedAsyncioTestCase):
    async def test_all_succeed(self):
        sent = []

        async def send_fn(recipient):
            sent.append(recipient)

        sleeps = []

        async def fake_sleep(s):
            sleeps.append(s)

        outcome = await deliver_with_rate_limit(
            ["u1", "u2", "u3"], send_fn, min_interval_seconds=0.01, sleep_fn=fake_sleep,
        )
        self.assertEqual(outcome.succeeded, ["u1", "u2", "u3"])
        self.assertEqual(outcome.failed, [])
        self.assertEqual(len(sleeps), 3)

    async def test_respects_rate_limit_interval(self):
        async def send_fn(recipient):
            pass

        sleeps = []

        async def fake_sleep(s):
            sleeps.append(s)

        await deliver_with_rate_limit(["u1", "u2"], send_fn, min_interval_seconds=0.05, sleep_fn=fake_sleep)
        self.assertTrue(all(s == 0.05 for s in sleeps))

    async def test_retry_after_honored_as_a_floor(self):
        attempts = {"u1": 0}

        async def send_fn(recipient):
            attempts[recipient] += 1
            if attempts[recipient] == 1:
                raise FakeRetryAfter(retry_after=3)

        sleeps = []

        async def fake_sleep(s):
            sleeps.append(s)

        outcome = await deliver_with_rate_limit(["u1"], send_fn, min_interval_seconds=0.01, sleep_fn=fake_sleep)
        self.assertEqual(outcome.succeeded, ["u1"])
        self.assertIn(3.0, sleeps, "Must wait exactly the server-specified retry_after")

    async def test_persistent_failure_recorded_not_raised(self):
        async def send_fn(recipient):
            raise RuntimeError("blocked by user")

        async def fake_sleep(s):
            pass

        outcome = await deliver_with_rate_limit(["u1", "u2"], send_fn, sleep_fn=fake_sleep)
        self.assertEqual(outcome.succeeded, [])
        self.assertEqual(len(outcome.failed), 2)
        self.assertIn("blocked by user", outcome.failed[0][1])

    async def test_one_failure_does_not_block_remaining_recipients(self):
        async def send_fn(recipient):
            if recipient == "u2":
                raise RuntimeError("fail")

        async def fake_sleep(s):
            pass

        outcome = await deliver_with_rate_limit(["u1", "u2", "u3"], send_fn, sleep_fn=fake_sleep)
        self.assertEqual(set(outcome.succeeded), {"u1", "u3"})
        self.assertEqual([f[0] for f in outcome.failed], ["u2"])


if __name__ == "__main__":
    unittest.main()
