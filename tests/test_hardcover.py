import unittest
from unittest.mock import Mock, patch

import requests

from hardcover import HardcoverAPIError, get_book_statuses, get_currently_reading


class HardcoverTests(unittest.TestCase):
    def response_with(self, payload):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = payload
        session = Mock()
        session.post.return_value = response
        return patch("hardcover._session", return_value=session)

    def test_non_object_payload_raises_controlled_error(self):
        with self.response_with([]):
            with self.assertRaisesRegex(HardcoverAPIError, "JSON object"):
                get_currently_reading("token")

    def test_non_object_book_entry_raises_controlled_error(self):
        payload = {"data": {"me": [{"user_books": [None]}]}}
        with self.response_with(payload):
            with self.assertRaisesRegex(HardcoverAPIError, "book entry"):
                get_currently_reading("token")

    def test_non_object_book_raises_controlled_error(self):
        payload = {"data": {"me": [{"user_books": [{"book": []}]}]}}
        with self.response_with(payload):
            with self.assertRaisesRegex(HardcoverAPIError, "book object"):
                get_currently_reading("token")

    def test_malformed_reading_progress_raises_controlled_error(self):
        payload = {
            "data": {"me": [{"user_books": [{"book": {}, "user_book_reads": [None]}]}]}
        }
        with self.response_with(payload):
            with self.assertRaisesRegex(HardcoverAPIError, "reading progress"):
                get_currently_reading("token")

    def test_non_object_status_entry_raises_controlled_error(self):
        payload = {"data": {"me": [{"user_books": [None]}]}}
        with self.response_with(payload):
            with self.assertRaisesRegex(HardcoverAPIError, "book entry"):
                get_book_statuses("token", [1])

    def test_non_object_me_entry_raises_controlled_error(self):
        payload = {"data": {"me": [None]}}
        with self.response_with(payload):
            with self.assertRaisesRegex(HardcoverAPIError, "account entry"):
                get_currently_reading("token")

    def test_non_list_contributions_raise_controlled_error(self):
        payload = {
            "data": {
                "me": [
                    {
                        "user_books": [
                            {
                                "id": 1,
                                "book": {"id": 2, "title": "Dune", "contributions": {}},
                            }
                        ]
                    }
                ]
            }
        }
        with self.response_with(payload):
            with self.assertRaisesRegex(HardcoverAPIError, "contributions"):
                get_currently_reading("token")

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
