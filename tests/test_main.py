import os
import tempfile
import unittest
from unittest.mock import patch

from config import Config
from hardcover import HardcoverAPIError
import main
from sync_result import SyncResult
from sync_state import empty_state, load_state, save_state


BOOK = {
    "id": "7",
    "user_book_id": 7,
    "book_id": 70,
    "edition_id": 700,
    "title": "Dune",
    "author": "Frank Herbert",
    "total_pages": 500,
    "progress_pages": 100,
    "progress_percent": 20.0,
}


class FakeAdapter:
    update_result = SyncResult.ok("https://example.test/dune")

    def __init__(self, *_):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def login(self):
        return True

    def update_progress(self, _book, _url=None):
        return self.update_result

    def mark_finished(self, _book, _url=None):
        return SyncResult.ok(_url)


class MainTests(unittest.TestCase):
    def setUp(self):
        self.storygraph_cookie_patch = patch.object(
            main,
            "STORYGRAPH_COOKIES",
            os.path.join(tempfile.gettempdir(), "missing-storygraph-cookie.json"),
        )
        self.storygraph_cookie_patch.start()

    def tearDown(self):
        self.storygraph_cookie_patch.stop()

    def config(self, path):
        return Config("token", "email", "password", "", "", 1800, path)

    def test_source_failure_preserves_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            state = empty_state()
            state["source_books"]["7"] = BOOK
            save_state(path, state)
            with open(path, "rb") as handle:
                before = handle.read()
            with patch.object(
                main,
                "get_currently_reading",
                side_effect=HardcoverAPIError("offline"),
            ):
                main.run_sync(self.config(path))
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), before)

    def test_malformed_finished_status_skips_destinations_and_preserves_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            state = empty_state()
            state["source_books"]["7"] = BOOK
            save_state(path, state)
            with open(path, "rb") as handle:
                before = handle.read()
            with (
                patch.object(main, "get_currently_reading", return_value=[]),
                patch.object(
                    main,
                    "get_book_statuses",
                    side_effect=HardcoverAPIError(
                        "Hardcover returned a book without stable IDs or title"
                    ),
                ),
                patch.object(main, "GoodreadsSync") as destination,
            ):
                main.run_sync(self.config(path))
            destination.assert_not_called()
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), before)

    def test_failed_destination_operation_remains_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            adapter = FakeAdapter()
            adapter.update_result = SyncResult.failed("temporary")
            with (
                patch.object(main, "get_currently_reading", return_value=[BOOK]),
                patch.object(main, "get_book_statuses", return_value={}),
                patch.object(main, "GoodreadsSync", return_value=adapter),
            ):
                main.run_sync(self.config(path))
            state = load_state(path)
            self.assertNotIn("7", state["destinations"]["goodreads"]["books"])

    def test_destination_exception_does_not_block_later_books(self):
        class RaisingAdapter(FakeAdapter):
            def update_progress(self, book, _url=None):
                if book["id"] == "7":
                    raise RuntimeError("browser tab crashed")
                return SyncResult.ok(f"https://example.test/{book['id']}")

        second = dict(BOOK, id="8", user_book_id=8, book_id=80, title="Foundation")
        destination = {"books": {}, "mappings": {}}
        counts = main._sync_destination(
            "Goodreads", RaisingAdapter(), destination, {"7": BOOK, "8": second}, {}
        )
        self.assertEqual(counts, (1, 1, 0))
        self.assertNotIn("7", destination["books"])
        self.assertIn("8", destination["books"])

    def test_success_records_destination_and_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            with (
                patch.object(main, "get_currently_reading", return_value=[BOOK]),
                patch.object(main, "get_book_statuses", return_value={}),
                patch.object(main, "GoodreadsSync", FakeAdapter),
            ):
                main.run_sync(self.config(path))
            state = load_state(path)
            self.assertEqual(
                state["destinations"]["goodreads"]["mappings"]["7"],
                "https://example.test/dune",
            )

    def test_finished_book_is_persisted_until_destination_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            state = empty_state()
            state["source_books"]["7"] = BOOK
            save_state(path, state)
            finished = dict(BOOK, status_id=3)
            with (
                patch.object(main, "get_currently_reading", return_value=[]),
                patch.object(main, "get_book_statuses", return_value={"7": finished}),
                patch.object(main, "GoodreadsSync", FakeAdapter),
            ):
                main.run_sync(self.config(path))
            state = load_state(path)
            self.assertIn("7", state["pending_finished"])
            self.assertEqual(
                state["destinations"]["goodreads"]["books"]["7"], "finished"
            )

    def test_missing_status_record_is_preserved_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            state = empty_state()
            state["source_books"]["7"] = BOOK
            save_state(path, state)
            with (
                patch.object(main, "get_currently_reading", return_value=[]),
                patch.object(main, "get_book_statuses", return_value={}),
                patch.object(main, "GoodreadsSync", FakeAdapter),
            ):
                main.run_sync(self.config(path))
            self.assertIn("7", load_state(path)["source_books"])


if __name__ == "__main__":
    unittest.main()
