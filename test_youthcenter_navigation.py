import unittest
from unittest.mock import patch

from youth_data_collector import PLAYWRIGHT_NAVIGATION_TIMEOUT_MS, goto_with_retry


class FakePage:
    def __init__(self, failures=0):
        self.failures = failures
        self.calls = []

    def goto(self, url, **options):
        self.calls.append((url, options))
        if len(self.calls) <= self.failures:
            raise TimeoutError("test timeout")


class YouthCenterNavigationTests(unittest.TestCase):
    @patch("youth_data_collector.time.sleep")
    def test_navigation_retries_with_domcontentloaded(self, _sleep):
        page = FakePage(failures=1)
        self.assertTrue(goto_with_retry(page, "https://example.test", attempts=2))
        self.assertEqual(len(page.calls), 2)
        self.assertEqual(page.calls[0][1]["wait_until"], "domcontentloaded")
        self.assertEqual(page.calls[0][1]["timeout"], PLAYWRIGHT_NAVIGATION_TIMEOUT_MS)

    @patch("youth_data_collector.time.sleep")
    def test_navigation_returns_false_after_all_attempts(self, _sleep):
        page = FakePage(failures=3)
        self.assertFalse(goto_with_retry(page, "https://example.test", attempts=2))


if __name__ == "__main__":
    unittest.main()
