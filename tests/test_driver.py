import unittest
import shutil
from unittest.mock import patch

from driver import browser_user_agent, build_options, chrome_log_path


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

    def test_build_options_uses_isolated_writable_chrome_dirs(self):
        options, _, runtime_dirs = build_options()
        try:
            arguments = options.arguments
            self.assertIn("--remote-debugging-port=0", arguments)
            self.assertIn("--disable-dev-shm-usage", arguments)
            self.assertTrue(
                any(arg.startswith("--user-data-dir=") for arg in arguments)
            )
            self.assertTrue(any(arg.startswith("--data-path=") for arg in arguments))
            self.assertTrue(
                any(arg.startswith("--disk-cache-dir=") for arg in arguments)
            )
            for runtime_dir in runtime_dirs.values():
                self.assertTrue(runtime_dir)
        finally:
            for runtime_dir in runtime_dirs.values():
                shutil.rmtree(runtime_dir, ignore_errors=True)

    def test_chromedriver_log_path_can_be_overridden(self):
        with patch.dict("os.environ", {"CHROMEDRIVER_LOG": "/tmp/custom.log"}):
            self.assertEqual(chrome_log_path().as_posix(), "/tmp/custom.log")


if __name__ == "__main__":
    unittest.main()
