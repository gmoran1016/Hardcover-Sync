import unittest

from driver import browser_user_agent


class DriverTests(unittest.TestCase):
    def test_replaces_headless_marker_without_changing_version(self):
        original = (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) HeadlessChrome/149.0.0.0 Safari/537.36"
        )
        expected = original.replace("HeadlessChrome/", "Chrome/")
        self.assertEqual(browser_user_agent(original), expected)

    def test_normal_user_agent_is_unchanged(self):
        original = "Mozilla/5.0 Chrome/149.0.0.0 Safari/537.36"
        self.assertEqual(browser_user_agent(original), original)


if __name__ == "__main__":
    unittest.main()
