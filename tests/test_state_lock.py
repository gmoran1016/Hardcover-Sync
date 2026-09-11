import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import sync_state


class StateLockTests(unittest.TestCase):
    def test_another_process_cannot_enter_until_owner_releases_lock(self):
        child_code = """
import sys
from sync_state import state_lock, StateError
try:
    with state_lock(sys.argv[1]):
        print('acquired')
except StateError as exc:
    print(str(exc))
    sys.exit(3)
"""
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "state.json")
            with sync_state.state_lock(path):
                locked = subprocess.run(
                    [sys.executable, "-c", child_code, path],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(locked.returncode, 3, locked.stderr)
                self.assertIn("already in use", locked.stdout)
            released = subprocess.run(
                [sys.executable, "-c", child_code, path],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(released.returncode, 0, released.stderr)
            self.assertIn("acquired", released.stdout)

    def test_abrupt_process_exit_releases_lock_without_deleting_lock_file(self):
        child_code = """
import os, sys
from sync_state import state_lock
with state_lock(sys.argv[1]):
    os._exit(0)
"""
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "state.json")
            child = subprocess.run(
                [sys.executable, "-c", child_code, path],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(child.returncode, 0, child.stderr)
            self.assertTrue(Path(f"{path}.lock").exists())
            with sync_state.state_lock(path):
                pass

    def test_recovery_obeys_the_same_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "state.json")
            with sync_state.state_lock(path):
                with self.assertRaisesRegex(sync_state.StateError, "already in use"):
                    sync_state.recover_state(path)
