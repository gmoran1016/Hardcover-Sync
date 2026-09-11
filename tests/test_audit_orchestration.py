"""Regression coverage for synchronization lifecycle and operator outcomes."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import main
from config import Config
from hardcover import HardcoverAPIError
from sync_result import SyncResult
from sync_state import empty_state, load_state, progress_signature, save_state


BOOK = {
    "id": "7",
    "user_book_id": 7,
    "book_id": 70,
    "edition_id": 700,
    "read_id": 7000,
    "title": "Dune",
    "author": "Frank Herbert",
    "total_pages": 500,
    "progress_pages": 100,
    "progress_percent": 20.0,
}


class RecordingAdapter:
    def __init__(self):
        self.operations = []
        self.logins = 0
        self.success = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def login(self):
        self.logins += 1
        return True

    def update_progress(self, book, url=None):
        self.operations.append(("update", book["id"]))
        return SyncResult.ok(url) if self.success else SyncResult.failed("offline")

    def mark_finished(self, book, url=None):
        self.operations.append(("finish", book["id"]))
        return SyncResult.ok(url) if self.success else SyncResult.failed("offline")


class AuditOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.json"
        self.config = Config("token", "email", "password", "", "", 60, str(self.path))
        self.adapter = RecordingAdapter()
        self.enterContext(
            patch.object(main, "_enabled", side_effect=lambda _c, e, _p: bool(e))
        )
        self.enterContext(
            patch.object(main, "GoodreadsSync", return_value=self.adapter)
        )
        self.enterContext(
            patch.object(
                main, "get_currently_reading", return_value=[copy.deepcopy(BOOK)]
            )
        )
        self.statuses = self.enterContext(
            patch.object(main, "get_book_statuses", return_value={})
        )

    def test_restarted_read_cancels_queued_completion(self):
        state = empty_state()
        state["pending_finished"]["7"] = dict(BOOK, status_id=3, read_id=6999)
        state["destinations"]["goodreads"]["books"]["7"] = "finished"
        save_state(str(self.path), state)
        main.run_sync(self.config)
        main.run_sync(self.config)
        self.assertEqual(self.adapter.operations, [("update", "7")])
        self.assertNotIn("7", load_state(str(self.path))["pending_finished"])

    def test_retracted_finish_is_revalidated_before_mutation(self):
        state = empty_state()
        state["pending_finished"]["7"] = dict(BOOK, status_id=3)
        save_state(str(self.path), state)
        self.statuses.return_value = {"7": dict(BOOK, status_id=4)}
        with patch.object(main, "get_currently_reading", return_value=[]):
            main.run_sync(self.config)
        self.assertEqual(self.adapter.operations, [])
        self.assertNotIn("7", load_state(str(self.path))["pending_finished"])

    def test_invalid_storage_prevents_destination_mutation(self):
        blocker = Path(self.temp.name) / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        config = Config(
            "token", "email", "password", "", "", 60, str(blocker / "state.json")
        )
        main.run_sync(config)
        self.assertEqual(self.adapter.operations, [])

    def test_unchanged_destination_does_not_authenticate(self):
        state = empty_state()
        state["source_books"]["7"] = dict(BOOK)
        state["destinations"]["goodreads"]["books"]["7"] = progress_signature(BOOK)
        save_state(str(self.path), state)
        main.run_sync(self.config)
        self.assertEqual(self.adapter.logins, 0)

    def test_destination_health_reports_verified_success_and_pending_count(self):
        main.run_sync(self.config)
        destination_health = load_state(str(self.path))["health"]["destinations"][
            "goodreads"
        ]
        self.assertIn("last_verified_success", destination_health)
        self.assertEqual(destination_health["pending"], 0)

    def test_once_api_failure_exits_nonzero(self):
        with (
            patch.object(main, "load_config", return_value=self.config),
            patch.object(
                main, "get_currently_reading", side_effect=HardcoverAPIError("offline")
            ),
            self.assertRaises(SystemExit) as stopped,
        ):
            main.main(["--once"])
        self.assertNotEqual(stopped.exception.code, 0)

    def test_auth_diagnostics_without_destinations_fails(self):
        with patch.object(main, "_enabled", return_value=False):
            self.assertFalse(main.run_auth_diagnostics(self.config))

    def test_completed_destination_is_saved_before_next_session_crashes(self):
        config = Config(
            "token", "email", "password", "sg", "password", 60, str(self.path)
        )
        with (
            patch.object(main, "StorygraphSync", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            main.run_sync(config)
        saved = load_state(str(self.path))
        self.assertIn("7", saved["destinations"]["goodreads"]["books"])


if __name__ == "__main__":
    unittest.main()
