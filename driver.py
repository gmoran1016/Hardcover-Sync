"""Shared Selenium WebDriver factory with bounded diagnostics and cleanup."""

import logging
import os
import shutil
import tempfile
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

os.environ.setdefault("SE_AVOID_STATS", "true")
logger = logging.getLogger(__name__)


def browser_user_agent(user_agent: str) -> str:
    return user_agent.replace("HeadlessChrome/", "Chrome/")


def set_user_agent(
    driver: webdriver.Chrome, user_agent: str, user_agent_metadata: dict | None = None
) -> None:
    if user_agent:
        parameters = {"userAgent": user_agent}
        if user_agent_metadata:
            parameters["userAgentMetadata"] = user_agent_metadata
        driver.execute_cdp_cmd("Network.setUserAgentOverride", parameters)


class ChromeLog:
    """Small bounded binary log sink compatible with Selenium's Service."""

    def __init__(self, path: Path, max_bytes: int = 256 * 1024):
        self.path = Path(path)
        self.max_bytes = max(4096, int(max_bytes))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "ab", buffering=0)
        self.stream = self
        self._closed = False

    def _rotate(self, incoming: int) -> None:
        try:
            size = self._handle.tell()
        except (OSError, ValueError):
            size = self.path.stat().st_size if self.path.exists() else 0
        if size + incoming <= self.max_bytes:
            return
        self._handle.flush()
        self._handle.close()
        for index in (2, 1):
            old = Path(f"{self.path}.{index}")
            new = Path(f"{self.path}.{index + 1}")
            if old.exists():
                if index == 2:
                    old.unlink(missing_ok=True)
                else:
                    old.replace(new)
        if self.path.exists():
            self.path.replace(Path(f"{self.path}.1"))
        self._handle = open(self.path, "ab", buffering=0)

    def write(self, value):
        if isinstance(value, str):
            value = value.encode()
        if not value:
            return 0
        # Keep a single oversized line bounded as well.
        if len(value) > self.max_bytes:
            value = value[-self.max_bytes :]
        self._rotate(len(value))
        self._handle.write(value)
        return len(value)

    def flush(self):
        self._handle.flush()

    def fileno(self):
        return self._handle.fileno()

    def close(self):
        if self._closed:
            return
        try:
            self._handle.flush()
        finally:
            self._handle.close()
            self._closed = True


def build_options() -> tuple[Options, bool, dict[str, str]]:
    options = Options()
    runtime_dirs: dict[str, str] = {}
    try:
        for name, prefix in (
            ("user_data", "hardcover-chrome-profile-"),
            ("data_path", "hardcover-chrome-data-"),
            ("cache", "hardcover-chrome-cache-"),
        ):
            runtime_dirs[name] = tempfile.mkdtemp(prefix=prefix)
    except Exception:
        for runtime_dir in runtime_dirs.values():
            shutil.rmtree(runtime_dir, ignore_errors=True)
        raise

    headless = os.getenv("CHROME_HEADLESS", "1").lower() not in ("0", "false", "no")
    if headless:
        options.add_argument("--headless=new")
    if os.getenv("CHROME_NO_SANDBOX", "0").lower() in ("1", "true", "yes"):
        options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--remote-debugging-port=0")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(f"--user-data-dir={runtime_dirs['user_data']}")
    options.add_argument(f"--data-path={runtime_dirs['data_path']}")
    options.add_argument(f"--disk-cache-dir={runtime_dirs['cache']}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    # Selenium exposes this mapping to the driver and tests can inspect it before
    # startup.  The same values are applied again below for older Selenium builds.
    options.timeouts = {"implicit": 0, "pageLoad": 30000, "script": 15000}

    chrome_bin = os.getenv("CHROME_BIN")
    if chrome_bin and os.path.exists(chrome_bin):
        options.binary_location = chrome_bin
        logger.debug("Using Chrome binary: %s", chrome_bin)
    return options, headless, runtime_dirs


def chrome_log_path() -> Path:
    return Path(
        os.getenv(
            "CHROMEDRIVER_LOG",
            str(Path(tempfile.gettempdir()) / "hardcover-sync-chromedriver.log"),
        )
    )


def _cleanup(runtime_dirs: dict[str, str]) -> None:
    for runtime_dir in runtime_dirs.values():
        shutil.rmtree(runtime_dir, ignore_errors=True)


def create_driver() -> webdriver.Chrome:
    options, headless, runtime_dirs = build_options()
    log_path = chrome_log_path()
    log_sink = None
    driver = None
    try:
        log_sink = ChromeLog(log_path)
        chromedriver_path = os.getenv("CHROMEDRIVER_PATH")
        service = (
            Service(chromedriver_path, log_output=log_sink)
            if chromedriver_path and os.path.exists(chromedriver_path)
            else Service(log_output=log_sink)
        )
        driver = webdriver.Chrome(service=service, options=options)
        # Apply bounded browser-side timeouts and Selenium command timeout.
        for method, value in (
            ("set_page_load_timeout", 30),
            ("set_script_timeout", 15),
            ("implicitly_wait", 0),
        ):
            try:
                getattr(driver, method)(value)
            except Exception:
                logger.debug("Browser does not support %s", method, exc_info=True)
        try:
            client_config = driver.command_executor.client_config
            client_config.timeout = min(client_config.timeout or 45, 45)
        except Exception:
            pass

        original_quit = driver.quit

        def quit_and_cleanup() -> None:
            try:
                original_quit()
            finally:
                if log_sink is not None:
                    log_sink.close()
                _cleanup(runtime_dirs)

        driver.quit = quit_and_cleanup
        if headless:
            version = driver.execute_cdp_cmd("Browser.getVersion", {})
            set_user_agent(driver, browser_user_agent(version.get("userAgent", "")))
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {
                    "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                },
            )
        return driver
    except Exception:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
        if log_sink is not None:
            try:
                log_sink.close()
            except Exception:
                pass
        _cleanup(runtime_dirs)
        logger.exception("ChromeDriver failed to start; see %s", log_path)
        raise
