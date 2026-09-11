import unittest

from goodreads import GoodreadsSync
from tests.destination_browser_fixture import BOOK, GR_URL, DestinationBrowserCase


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


if __name__ == "__main__":
    unittest.main()
