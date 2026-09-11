import unittest
import shutil
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import driver as driver_module
from driver import browser_user_agent, build_options, chrome_log_path


class DriverTests(unittest.TestCase):
    def test_replaces_headless_marker_without_changing_version(self):
        original = (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) HeadlessChrome/149.0.0.0 Safari/537.36"
        )
        expected = original.replace("HeadlessChrome/", "Chrome/")
        self.assertEqual(browser_user_agent(original), expected)

    def test_normal_user_agent_is_unchanged(self):
        original = "Mozilla/5.0 Chrome/149.0.0.0 Safari/537.36"
        self.assertEqual(browser_user_agent(original), original)

    def test_build_options_uses_isolated_writable_chrome_dirs(self):
        options, _, runtime_dirs = build_options()
        try:
            arguments = options.arguments
            self.assertIn("--remote-debugging-port=0", arguments)
            self.assertIn("--disable-dev-shm-usage", arguments)
            self.assertNotIn(
                "useAutomationExtension",
                options.experimental_options,
            )
            self.assertTrue(
                any(arg.startswith("--user-data-dir=") for arg in arguments)
            )
            self.assertTrue(any(arg.startswith("--data-path=") for arg in arguments))
            self.assertTrue(
                any(arg.startswith("--disk-cache-dir=") for arg in arguments)
            )
            for runtime_dir in runtime_dirs.values():
                self.assertTrue(runtime_dir)
        finally:
            for runtime_dir in runtime_dirs.values():
                shutil.rmtree(runtime_dir, ignore_errors=True)

    def test_chromedriver_log_path_can_be_overridden(self):
        with patch.dict("os.environ", {"CHROMEDRIVER_LOG": "/tmp/custom.log"}):
            self.assertEqual(chrome_log_path().as_posix(), "/tmp/custom.log")

    def test_default_log_uses_the_platform_temporary_directory(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(chrome_log_path().parent, Path(tempfile.gettempdir()))

    def test_partial_runtime_directory_creation_is_cleaned_up(self):
        make_directory = tempfile.mkdtemp
        for failing_call in (2, 3):
            with self.subTest(failing_call=failing_call):
                with tempfile.TemporaryDirectory() as temporary:
                    created = []

                    def allocate(*, prefix):
                        if len(created) + 1 == failing_call:
                            raise OSError("directory allocation failed")
                        path = make_directory(prefix=prefix, dir=temporary)
                        created.append(Path(path))
                        return path

                    with patch.object(driver_module.tempfile, "mkdtemp", allocate):
                        with self.assertRaisesRegex(OSError, "allocation failed"):
                            build_options()
                    self.assertFalse(any(path.exists() for path in created))

    def test_log_directory_and_log_open_failures_clean_runtime_directories(self):
        make_directory = tempfile.mkdtemp
        for fail_parent in (True, False):
            with self.subTest(fail_parent=fail_parent):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    if fail_parent:
                        blocked = root / "blocked"
                        blocked.write_text("not a directory", encoding="utf-8")
                        log_path = blocked / "chromedriver.log"
                    else:
                        log_path = root / "directory-instead-of-log"
                        log_path.mkdir()
                    created = []

                    def allocate(*, prefix):
                        path = make_directory(prefix=prefix, dir=temporary)
                        created.append(Path(path))
                        return path

                    with (
                        patch.object(driver_module.tempfile, "mkdtemp", allocate),
                        patch.dict("os.environ", {"CHROMEDRIVER_LOG": str(log_path)}),
                    ):
                        with self.assertRaises(OSError):
                            driver_module.create_driver()
                    self.assertEqual(len(created), 3)
                    self.assertFalse(any(path.exists() for path in created))

    def test_startup_failure_flushes_and_closes_diagnostics(self):
        with tempfile.TemporaryDirectory() as temporary:
            log_path = Path(temporary, "chromedriver.log")

            def fail_startup(*, service, options):
                os.write(service.log_output.fileno(), b"startup failed\n")
                raise RuntimeError("browser unavailable")

            with (
                patch.dict("os.environ", {"CHROMEDRIVER_LOG": str(log_path)}),
                patch.object(driver_module.webdriver, "Chrome", fail_startup),
            ):
                with self.assertRaisesRegex(RuntimeError, "browser unavailable"):
                    driver_module.create_driver()
            self.assertIn("startup failed", log_path.read_text(encoding="utf-8"))
            log_path.rename(Path(temporary, "closed.log"))

    def test_browser_timeouts_and_quit_failure_still_release_resources(self):
        with tempfile.TemporaryDirectory() as temporary:
            log_path = Path(temporary, "chromedriver.log")
            runtime_paths = []
            remote = SimpleNamespace(
                command_executor=SimpleNamespace(
                    client_config=SimpleNamespace(timeout=None)
                )
            )

            def failing_quit():
                raise RuntimeError("browser quit failed")

            remote.quit = failing_quit

            def start_browser(*, service, options):
                self.assertIsNotNone(options.timeouts)
                self.assertLessEqual(options.timeouts["pageLoad"], 30000)
                self.assertLessEqual(options.timeouts["script"], 15000)
                self.assertEqual(options.timeouts["implicit"], 0)
                runtime_paths.extend(
                    Path(argument.split("=", 1)[1])
                    for argument in options.arguments
                    if argument.startswith(
                        ("--user-data-dir=", "--data-path=", "--disk-cache-dir=")
                    )
                )
                return remote

            with (
                patch.dict(
                    "os.environ",
                    {"CHROMEDRIVER_LOG": str(log_path), "CHROME_HEADLESS": "0"},
                ),
                patch.object(driver_module.webdriver, "Chrome", start_browser),
            ):
                browser = driver_module.create_driver()
                self.assertLessEqual(browser.command_executor.client_config.timeout, 45)
                with self.assertRaisesRegex(RuntimeError, "browser quit failed"):
                    browser.quit()
            self.assertFalse(any(path.exists() for path in runtime_paths))
            log_path.rename(Path(temporary, "closed.log"))

    def test_browser_diagnostics_rotate_during_a_single_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary, "chromedriver.log")
            self.assertTrue(hasattr(driver_module, "ChromeLog"))
            sink = driver_module.ChromeLog(path, max_bytes=32768)
            try:
                for _ in range(20):
                    sink.stream.write(b"diagnostic line\n" * 1024)
                sink.stream.write(b"last diagnostic\n")
            finally:
                sink.close()
            logs = list(Path(temporary).glob("chromedriver.log*"))
            self.assertEqual(len(logs), 3)
            self.assertTrue(all(log.stat().st_size <= 32768 for log in logs))
            self.assertIn("last diagnostic", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
