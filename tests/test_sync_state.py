import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sync_state

from sync_state import SCHEMA_VERSION, StateError, empty_state, load_state, save_state


class StateTests(unittest.TestCase):
    def test_atomic_round_trip_and_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            first = empty_state()
            save_state(path, first)
            second = empty_state()
            second["source_books"]["12"] = {"id": "12", "title": "Dune"}
            save_state(path, second)
            self.assertEqual(load_state(path)["source_books"]["12"]["title"], "Dune")
            self.assertTrue(os.path.exists(f"{path}.bak"))

    def test_migrates_legacy_title_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"books": [{"title": "Dune"}]}, handle)
            state = load_state(path)
            self.assertEqual(state["schema_version"], SCHEMA_VERSION)
            self.assertEqual(len(state["source_books"]), 1)

    def test_corrupt_state_is_not_overwritten_during_load(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{broken")
            with self.assertRaises(StateError):
                load_state(path)
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "{broken")

    def test_non_object_state_raises_controlled_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump([], handle)
            with self.assertRaisesRegex(StateError, "top-level object"):
                load_state(path)

    def test_invalid_schema_v2_collections_raise_controlled_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "state.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"schema_version": 2, "destinations": []}, handle)
            with self.assertRaisesRegex(StateError, "destinations"):
                load_state(path)

    def test_unsupported_versions_preserve_populated_primary(self):
        for version in (3, 0, None, "2", True):
            with (
                self.subTest(version=version),
                tempfile.TemporaryDirectory() as directory,
            ):
                path = Path(directory) / "state.json"
                data = empty_state()
                data["source_books"]["12"] = {"id": "12", "title": "Dune"}
                data["schema_version"] = version
                original = json.dumps(data)
                path.write_text(original, encoding="utf-8")
                with self.assertRaisesRegex(StateError, "version"):
                    load_state(str(path))
                self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_unrecognized_unversioned_state_is_not_migrated_to_empty(self):
        for data in ({}, {"source_books": {}}, {"books": {}}, {"books": [None]}):
            with self.subTest(data=data), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "state.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(StateError):
                    load_state(str(path))

    def test_explicit_legacy_version_and_minimal_records_remain_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(
                json.dumps({"schema_version": 1, "books": [{"title": "Dune"}]}),
                encoding="utf-8",
            )
            state = load_state(str(path))
            save_state(str(path), state)
            self.assertEqual(
                next(iter(load_state(str(path))["source_books"].values()))["title"],
                "Dune",
            )

    def test_nested_book_records_are_validated_before_use(self):
        records = (
            None,
            [],
            {},
            {"id": "12", "title": " "},
            {"id": "99", "title": "Dune"},
            {"title": "Dune", "user_book_id": "bad"},
            {"title": "Dune", "author": []},
            {"title": "Dune", "progress_pages": -1},
            {"title": "Dune", "progress_percent": float("nan")},
            {"title": "Dune", "progress_percent": 101},
        )
        for collection in ("source_books", "pending_finished"):
            for record in records:
                with (
                    self.subTest(collection=collection, record=record),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    path = Path(directory) / "state.json"
                    state = empty_state()
                    state[collection]["12"] = record
                    path.write_text(json.dumps(state), encoding="utf-8")
                    with self.assertRaises(StateError):
                        load_state(str(path))

    def test_nested_destination_fields_are_validated(self):
        changes = (
            ("books", {"12": None}),
            ("mappings", {"12": []}),
            (
                "failures",
                {"12": {"reason": "offline", "retryable": "yes", "updated_at": 10}},
            ),
        )
        for field, value in changes:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "state.json"
                state = empty_state()
                state["destinations"]["goodreads"][field] = value
                path.write_text(json.dumps(state), encoding="utf-8")
                with self.assertRaises(StateError):
                    load_state(str(path))

    def test_health_cooldown_and_failure_metadata_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            state = empty_state()
            state["health"] = {
                "last_attempt": 100.5,
                "destinations": {"goodreads": {"pending": 1}},
            }
            state["source_retry_at"] = 86400.5
            state["destinations"]["goodreads"]["failures"] = {
                "12": {
                    "reason": "login failed",
                    "retryable": False,
                    "updated_at": 100.5,
                }
            }
            save_state(str(path), state)
            self.assertEqual(load_state(str(path)), state)

    def test_missing_primary_with_backup_requires_explicit_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            backup = Path(f"{path}.bak")
            backup.write_text(json.dumps(empty_state()), encoding="utf-8")
            with self.assertRaisesRegex(StateError, "backup|recover"):
                load_state(str(path))
            self.assertFalse(path.exists())

    def test_recovery_preserves_corrupt_primary_and_valid_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("{broken", encoding="utf-8")
            backup = Path(f"{path}.bak")
            data = empty_state()
            data["source_books"]["12"] = {"id": "12", "title": "Dune"}
            original_backup = json.dumps(data)
            backup.write_text(original_backup, encoding="utf-8")
            preserved = sync_state.recover_state(str(path))
            self.assertEqual(Path(preserved).read_text(encoding="utf-8"), "{broken")
            self.assertEqual(
                load_state(str(path))["source_books"]["12"]["title"], "Dune"
            )
            self.assertEqual(backup.read_text(encoding="utf-8"), original_backup)

    def test_recovery_with_missing_primary_restores_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            Path(f"{path}.bak").write_text(json.dumps(empty_state()), encoding="utf-8")
            self.assertIn("missing", sync_state.recover_state(str(path)))
            self.assertEqual(load_state(str(path)), empty_state())

    def test_recovery_refuses_invalid_backup_without_touching_primary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("original evidence", encoding="utf-8")
            Path(f"{path}.bak").write_text("{bad backup", encoding="utf-8")
            with self.assertRaises(StateError):
                sync_state.recover_state(str(path))
            self.assertEqual(path.read_text(encoding="utf-8"), "original evidence")
            self.assertFalse(list(Path(directory).glob("state.json.corrupt.*")))

    def test_save_validates_before_replacing_primary_or_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            save_state(str(path), empty_state())
            old_primary = path.read_bytes()
            state = empty_state()
            state["source_books"]["12"] = None
            with self.assertRaises(StateError):
                save_state(str(path), state)
            self.assertEqual(path.read_bytes(), old_primary)
            self.assertFalse(Path(f"{path}.bak").exists())

    def test_save_refuses_to_replace_unreadable_primary_or_valid_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("{broken", encoding="utf-8")
            backup = Path(f"{path}.bak")
            backup.write_text(json.dumps(empty_state()), encoding="utf-8")
            before = backup.read_bytes()
            with self.assertRaises(StateError):
                save_state(str(path), empty_state())
            self.assertEqual(path.read_text(encoding="utf-8"), "{broken")
            self.assertEqual(backup.read_bytes(), before)

    def test_save_logs_directory_creation_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            blocker = Path(directory) / "blocker"
            blocker.write_text("file", encoding="utf-8")
            with self.assertLogs("sync_state", level="ERROR") as logs:
                with self.assertRaises((OSError, StateError)):
                    save_state(str(blocker / "state.json"), empty_state())
            self.assertIn("save", logs.output[0])

    def test_storage_probe_does_not_change_primary_backup_or_leave_temps(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("primary evidence", encoding="utf-8")
            backup = Path(f"{path}.bak")
            backup.write_text("backup evidence", encoding="utf-8")
            sync_state.probe_state_storage(str(path))
            self.assertEqual(path.read_text(encoding="utf-8"), "primary evidence")
            self.assertEqual(backup.read_text(encoding="utf-8"), "backup evidence")
            self.assertEqual(
                sorted(p.name for p in Path(directory).iterdir()),
                ["state.json", "state.json.bak"],
            )

    def test_storage_probe_reports_replace_failure_and_cleans_temps(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "sync_state.os.replace", side_effect=PermissionError("read-only")
            ):
                with self.assertRaises(StateError):
                    sync_state.probe_state_storage(str(Path(directory) / "state.json"))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_signatures_distinguish_rereads_editions_and_corrected_page_totals(self):
        book = {
            "id": "12",
            "book_id": 120,
            "read_id": 1,
            "edition_id": 100,
            "total_pages": 500,
            "progress_pages": 100,
            "progress_percent": 20,
        }
        original = sync_state.progress_signature(book)
        for field, value in (
            ("read_id", 2),
            ("edition_id", 101),
            ("total_pages", 600),
            ("progress_pages", 99),
            ("progress_percent", 19),
        ):
            with self.subTest(field=field):
                self.assertNotEqual(
                    sync_state.progress_signature(dict(book, **{field: value})),
                    original,
                )
        self.assertNotEqual(
            sync_state.finished_signature(book),
            sync_state.finished_signature(dict(book, read_id=2)),
        )


if __name__ == "__main__":
    unittest.main()
