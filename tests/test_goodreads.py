import json
import unittest
from unittest.mock import patch

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By

import goodreads
from goodreads import GoodreadsSync
from tests.destination_browser_fixture import BOOK, GR_URL, DestinationBrowserCase


class SearchTimeoutWait:
    def __init__(self, *_args):
        pass

    def until(self, _condition):
        raise TimeoutException()


class TextBody:
    def __init__(self, text):
        self.text = text


class AutocompleteDriver:
    def __init__(self, response):
        self.response = response
        self.visited = []

    def get(self, url):
        self.visited.append(url)

    def find_element(self, by, selector):
        if (by, selector) == (By.TAG_NAME, "body"):
            return TextBody(self.response)
        raise AssertionError((by, selector))


class GoodreadsBrowserTests(DestinationBrowserCase):
    def sync(self):
        sync = GoodreadsSync("", "")
        sync.driver = self.driver
        return sync

    def test_mapped_identity_controls_progress_despite_title_collisions(self):
        result = self.sync().update_progress(BOOK, GR_URL + ".Dune?ref=search")
        self.assertTrue(result.success, result.reason)
        self.assertEqual(self.store.books["1"]["pages"], 100)
        self.assertEqual(self.store.books["2"]["pages"], 30)
        self.assertEqual(self.store.books["3"]["pages"], 10)
        self.assertEqual(result.target_url, GR_URL)

    def test_unmapped_widget_uses_title_and_author_evidence(self):
        result = self.sync().update_progress(BOOK)
        self.assertTrue(result.success, result.reason)
        self.assertEqual(self.store.books["1"]["pages"], 100)
        self.assertEqual(self.store.books["2"]["pages"], 30)
        self.assertEqual(self.store.books["3"]["pages"], 10)
        self.assertEqual(result.target_url, GR_URL)

    def test_mapped_identity_controls_finish_action(self):
        result = self.sync().mark_finished(BOOK, GR_URL)
        self.assertTrue(result.success, result.reason)
        self.assertEqual(self.store.books["1"]["status"], "read")
        self.assertEqual(self.store.books["2"]["status"], "currently reading")
        self.assertEqual(self.store.books["3"]["status"], "currently reading")

    def test_closed_form_after_failed_save_does_not_acknowledge_progress(self):
        self.store.failed_save = True
        self.store.widgets = ["1"]
        result = self.sync().update_progress(BOOK, GR_URL)
        self.assertFalse(result.success)
        self.assertEqual(self.store.books["1"]["pages"], 75)
        self.assertEqual(len(self.store.mutations), 1)

    def test_stale_finish_button_does_not_acknowledge_failed_completion(self):
        self.store.failed_finish = True
        self.store.widgets = ["1"]
        result = self.sync().mark_finished(BOOK, GR_URL)
        self.assertFalse(result.success)
        self.assertEqual(self.store.books["1"]["status"], "currently reading")
        self.assertEqual(len(self.store.mutations), 1)

    def test_mapped_already_read_book_succeeds_without_widget_or_write(self):
        self.store.books["1"]["status"] = "read"
        result = self.sync().mark_finished(BOOK, GR_URL)
        self.assertTrue(result.success, result.reason)
        self.assertEqual(result.target_url, GR_URL)
        self.assertEqual(self.store.mutations, [])

    def test_reordered_widgets_still_update_the_mapped_book(self):
        self.store.widgets = ["3", "1", "2"]
        result = self.sync().update_progress(BOOK, GR_URL)
        self.assertTrue(result.success, result.reason)
        self.assertEqual(self.store.mutations[0]["id"], "1")

    def test_invalid_mapping_does_not_fall_back_to_a_widget(self):
        result = self.sync().update_progress(BOOK, "https://evil.test/book/show/1")
        self.assertFalse(result.success)
        self.assertFalse(result.retryable)
        self.assertEqual(self.store.mutations, [])
        self.assertEqual(self.store.opens, [])


class GoodreadsSearchTests(unittest.TestCase):
    def test_search_accepts_shared_author_for_multi_author_hardcover_book(self):
        response = json.dumps(
            [
                {
                    "bookTitleBare": "The Gathering Storm",
                    "bookUrl": "/book/show/1166599.The_Gathering_Storm",
                    "author": {"name": "Robert Jordan"},
                }
            ]
        )
        sync = GoodreadsSync("", "")
        sync.driver = AutocompleteDriver(response)

        with patch.object(goodreads, "WebDriverWait", SearchTimeoutWait):
            result = sync._search_book(
                "The Gathering Storm", ["Brandon Sanderson", "Robert Jordan"]
            )

        self.assertEqual(result, "https://www.goodreads.com/book/show/1166599")
        self.assertIn(
            "q=The+Gathering+Storm+Brandon+Sanderson+Robert+Jordan",
            sync.driver.visited[0],
        )

    def test_search_uses_goodreads_autocomplete_after_search_page_timeout(self):
        response = json.dumps(
            [
                {
                    "bookTitleBare": "Winter's Heart",
                    "bookUrl": "/book/show/13891.Winter_s_Heart",
                    "author": {"name": "Robert Jordan"},
                }
            ]
        )
        driver = AutocompleteDriver(response)
        sync = GoodreadsSync("", "")
        sync.driver = driver

        with patch.object(goodreads, "WebDriverWait", SearchTimeoutWait):
            result = sync._search_book("Winter's Heart", "Robert Jordan")

        self.assertEqual(result, "https://www.goodreads.com/book/show/13891")
        self.assertEqual(len(driver.visited), 2)
        self.assertIn("/book/auto_complete?format=json", driver.visited[1])

    def test_search_rejects_autocomplete_result_with_conflicting_author(self):
        response = json.dumps(
            [
                {
                    "bookTitleBare": "Winter's Heart",
                    "bookUrl": "/book/show/13891.Winter_s_Heart",
                    "author": {"name": "Another Author"},
                }
            ]
        )
        sync = GoodreadsSync("", "")
        sync.driver = AutocompleteDriver(response)

        with patch.object(goodreads, "WebDriverWait", SearchTimeoutWait):
            result = sync._search_book("Winter's Heart", "Robert Jordan")

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
