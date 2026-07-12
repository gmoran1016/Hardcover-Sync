import json
import os
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
