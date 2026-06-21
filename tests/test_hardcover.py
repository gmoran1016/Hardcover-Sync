import unittest
from unittest.mock import Mock, patch

import requests

from hardcover import HardcoverAPIError, get_currently_reading


class HardcoverTests(unittest.TestCase):
    def test_successful_empty_library_is_distinct_from_failure(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"data": {"me": [{"user_books": []}]}}
        session = Mock()
        session.post.return_value = response
        with patch("hardcover._session", return_value=session):
            self.assertEqual(get_currently_reading("token"), [])

    def test_request_failure_raises(self):
        session = Mock()
        session.post.side_effect = requests.Timeout("offline")
        with patch("hardcover._session", return_value=session):
            with self.assertRaises(HardcoverAPIError):
                get_currently_reading("token")

    def test_graphql_error_raises(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"errors": [{"message": "denied"}]}
        session = Mock()
        session.post.return_value = response
        with patch("hardcover._session", return_value=session):
            with self.assertRaises(HardcoverAPIError):
                get_currently_reading("token")

    def test_malformed_response_raises(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"data": {"me": None}}
        session = Mock()
        session.post.return_value = response
        with patch("hardcover._session", return_value=session):
            with self.assertRaises(HardcoverAPIError):
                get_currently_reading("token")


if __name__ == "__main__":
    unittest.main()
