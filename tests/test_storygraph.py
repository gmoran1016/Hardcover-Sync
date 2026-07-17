import unittest
from unittest.mock import Mock, patch

from selenium.webdriver.common.by import By

import storygraph
from storygraph import StorygraphSync


BOOK = {"title": "Winter's Heart", "author": "Robert Jordan"}
BOOK_URL = "https://app.thestorygraph.com/books/winters-heart"


class FakeElement:
    def __init__(self, text: str, displayed: bool = True):
        self.text = text
        self._displayed = displayed

    def is_displayed(self):
        return self._displayed


class FakeDriver:
    def __init__(self, status: str, finish_buttons: list[FakeElement]):
        self.status_labels = [FakeElement(status)]
        self.finish_buttons = finish_buttons
        self.clicked = []
        self.current_url = ""

    def get(self, url):
        self.current_url = url

    def find_elements(self, by, selector):
        if (by, selector) == (By.CSS_SELECTOR, ".read-status-label"):
            return self.status_labels
        if (by, selector) == (
            By.XPATH,
            '//button[normalize-space(.)="Dismiss"]',
        ):
            return []
        if (by, selector) == (By.CSS_SELECTOR, ".expand-dropdown-button"):
            return [FakeElement("expand")]
        if (by, selector) == (By.CSS_SELECTOR, ".mark-as-finished-btn"):
            return self.finish_buttons
        return []

    def execute_script(self, _script, *arguments):
        if arguments:
            self.clicked.append(arguments[0].text)


class StorygraphTests(unittest.TestCase):
    def run_finish(self, driver):
        sync = StorygraphSync("", "")
        sync.driver = driver
        wait = Mock()
        wait.until.return_value = True
        with (
            patch.object(storygraph.time, "sleep"),
            patch.object(storygraph, "WebDriverWait", return_value=wait),
        ):
            return sync.mark_finished(BOOK, BOOK_URL)

    def test_current_reading_status_ignores_hidden_labels(self):
        driver = FakeDriver("read", [])
        driver.status_labels = [
            FakeElement("read", displayed=False),
            FakeElement("  ReReading  "),
        ]
        self.assertEqual(storygraph.current_reading_status(driver), "rereading")

    def test_mark_finished_succeeds_without_click_when_status_is_read(self):
        driver = FakeDriver("read", [])
        result = self.run_finish(driver)
        self.assertTrue(result.success)
        self.assertEqual(result.target_url, BOOK_URL)
        self.assertEqual(driver.clicked, [])

    def test_mark_finished_clicks_action_for_currently_reading(self):
        driver = FakeDriver("currently reading", [FakeElement("finished")])
        result = self.run_finish(driver)
        self.assertTrue(result.success)
        self.assertIn("finished", driver.clicked)

    def test_mark_finished_fails_when_not_read_and_action_is_missing(self):
        driver = FakeDriver("currently reading", [])
        result = self.run_finish(driver)
        self.assertFalse(result.success)


if __name__ == "__main__":
    unittest.main()
