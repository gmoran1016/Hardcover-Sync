import unittest
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch

import container_entrypoint


class ContainerEntrypointTests(unittest.TestCase):
    def test_log_flushes_to_stdout(self):
        with patch("builtins.print") as output:
            container_entrypoint.log("hello")
        output.assert_called_once_with(
            "[hardcover-sync-entrypoint] hello",
            flush=True,
        )

    def test_clear_display_artifacts_removes_stale_lock_and_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory, ".X99-lock")
            socket = Path(directory, "X99")
            lock.touch()
            socket.touch()
            with patch.object(
                container_entrypoint,
                "display_paths",
                return_value=(lock, socket),
            ):
                container_entrypoint.clear_display_artifacts(":99")
            self.assertFalse(lock.exists())
            self.assertFalse(socket.exists())

    def test_wait_for_display_rejects_dead_xvfb(self):
        process = Mock()
        process.poll.return_value = 1
        with self.assertRaisesRegex(RuntimeError, "Xvfb exited with code 1"):
            container_entrypoint.wait_for_display(
                process,
                Path("missing"),
                timeout=0,
            )

    def test_supervise_returns_application_status_and_stops_xvfb(self):
        xvfb = Mock()
        xvfb.poll.return_value = None
        application = Mock()
        application.poll.return_value = 7
        with patch.object(container_entrypoint, "stop_process") as stop:
            status = container_entrypoint.supervise(xvfb, application)
        self.assertEqual(status, 7)
        stop.assert_called_once_with(xvfb)

    def test_supervise_fails_and_stops_application_when_xvfb_dies(self):
        xvfb = Mock()
        xvfb.poll.return_value = 3
        application = Mock()
        application.poll.return_value = None
        with patch.object(container_entrypoint, "stop_process") as stop:
            with self.assertRaisesRegex(RuntimeError, "Xvfb exited with code 3"):
                container_entrypoint.supervise(xvfb, application)
        stop.assert_called_once_with(application)


if __name__ == "__main__":
    unittest.main()
