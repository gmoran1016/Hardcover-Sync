import os
import unittest
from unittest.mock import patch

from config import ConfigError, load_config


class ConfigTests(unittest.TestCase):
    def test_rejects_invalid_interval(self):
        with (
            patch("config.load_dotenv"),
            patch.dict(
                os.environ,
                {"HARDCOVER_API_KEY": "token", "SYNC_INTERVAL_MINUTES": "0"},
                clear=True,
            ),
        ):
            with self.assertRaises(ConfigError):
                load_config()

    def test_accepts_cookie_only_destination_credentials(self):
        with (
            patch("config.load_dotenv"),
            patch.dict(
                os.environ,
                {"HARDCOVER_API_KEY": "token", "SYNC_INTERVAL_MINUTES": "30"},
                clear=True,
            ),
        ):
            config = load_config()
            self.assertEqual(config.goodreads_email, "")
            self.assertEqual(config.sync_interval_seconds, 1800)


if __name__ == "__main__":
    unittest.main()
