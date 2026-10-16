import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.scanner.rate_limiter import ActionRateLimiter, ExponentialBackoff, GradualJoinScheduler  # noqa: E402


class TestGradualJoinScheduler(unittest.TestCase):
    def test_first_join_has_no_delay(self):
        sched = GradualJoinScheduler(min_seconds_between_joins=30)
        self.assertEqual(sched.next_join_delay_seconds(), 0.0)

    def test_subsequent_joins_are_delayed(self):
        sched = GradualJoinScheduler(min_seconds_between_joins=30)
        sched.record_join()
        self.assertEqual(sched.next_join_delay_seconds(), 30.0)

    def test_max_joins_per_run_enforced(self):
        sched = GradualJoinScheduler(max_joins_per_run=2)
        self.assertTrue(sched.can_join_more())
        sched.record_join()
        self.assertTrue(sched.can_join_more())
        sched.record_join()
        self.assertFalse(sched.can_join_more(), "Must stop joining after max_joins_per_run is reached")


class TestExponentialBackoff(unittest.TestCase):
    def test_backoff_grows_exponentially(self):
        backoff = ExponentialBackoff(base_seconds=5, multiplier=2, max_seconds=1000)
        d1 = backoff.next_delay_seconds()
        d2 = backoff.next_delay_seconds()
        d3 = backoff.next_delay_seconds()
        self.assertEqual(d1, 5)
        self.assertEqual(d2, 10)
        self.assertEqual(d3, 20)

    def test_backoff_never_exceeds_max(self):
        backoff = ExponentialBackoff(base_seconds=5, multiplier=2, max_seconds=15)
        for _ in range(10):
            delay = backoff.next_delay_seconds()
            self.assertLessEqual(delay, 15)

    def test_server_specified_wait_is_a_floor_not_overridden(self):
        backoff = ExponentialBackoff(base_seconds=1, multiplier=2, max_seconds=1000)
        # Computed backoff would be tiny (1s), but Telegram said wait 120s —
        # the server-specified wait must win (never retry sooner than told).
        delay = backoff.next_delay_seconds(server_specified_wait=120)
        self.assertGreaterEqual(delay, 120)

    def test_reset(self):
        backoff = ExponentialBackoff(base_seconds=5, multiplier=2)
        backoff.next_delay_seconds()
        backoff.next_delay_seconds()
        backoff.reset()
        self.assertEqual(backoff.next_delay_seconds(), 5)


class TestActionRateLimiter(unittest.TestCase):
    def test_waits_the_remaining_interval(self):
        limiter = ActionRateLimiter(min_interval_seconds=10)
        sleep_calls = []
        limiter.wait_if_needed(sleep_fn=sleep_calls.append)  # first call: no prior action
        self.assertEqual(sleep_calls, [])

        # Simulate an action that happened "just now" by calling again immediately —
        # remaining wait should be close to the full interval (not exactly, due to
        # monotonic clock granularity, so just assert a sleep was requested).
        limiter.wait_if_needed(sleep_fn=sleep_calls.append)
        self.assertEqual(len(sleep_calls), 1)
        self.assertGreater(sleep_calls[0], 0)


class TestActionRateLimiterAsync(unittest.IsolatedAsyncioTestCase):
    async def test_async_wait_actually_sleeps(self):
        """
        Regression test for C04: the Telegram listener previously called
        wait_if_needed(sleep_fn=lambda s: None) — a silent no-op. This
        proves the async counterpart genuinely awaits asyncio.sleep.
        """
        import unittest.mock as mock

        limiter = ActionRateLimiter(min_interval_seconds=10)
        clock = {"t": 0.0}

        def fake_now():
            return clock["t"]

        with mock.patch("asyncio.sleep") as mock_sleep:
            async def fast_sleep(seconds):
                clock["t"] += seconds
            mock_sleep.side_effect = fast_sleep

            await limiter.wait_if_needed_async(now_fn=fake_now)
            mock_sleep.assert_not_called()

            clock["t"] = 2.0
            await limiter.wait_if_needed_async(now_fn=fake_now)
            mock_sleep.assert_called_once()
            waited = mock_sleep.call_args[0][0]
            self.assertAlmostEqual(waited, 8.0, places=5)


if __name__ == "__main__":
    unittest.main()
