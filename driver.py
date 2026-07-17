"""Shared Selenium WebDriver factory used by both Goodreads and StoryGraph modules."""

import os
import logging
import shutil
import tempfile
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

# Suppress Selenium's built-in Plausible telemetry
os.environ.setdefault("SE_AVOID_STATS", "true")

logger = logging.getLogger(__name__)


def browser_user_agent(user_agent: str) -> str:
    """Return the installed browser's normal UA without the headless marker."""
    return user_agent.replace("HeadlessChrome/", "Chrome/")


def set_user_agent(
    driver: webdriver.Chrome,
    user_agent: str,
    user_agent_metadata: dict | None = None,
) -> None:
    """Apply a browser identity before navigating to an authenticated site."""
    if user_agent:
        parameters = {"userAgent": user_agent}
        if user_agent_metadata:
            parameters["userAgentMetadata"] = user_agent_metadata
        driver.execute_cdp_cmd("Network.setUserAgentOverride", parameters)


def build_options() -> tuple[Options, bool, dict[str, str]]:
    """Build Chrome options and temporary runtime directories."""
    options = Options()
    runtime_dirs = {
        "user_data": tempfile.mkdtemp(prefix="hardcover-chrome-profile-"),
        "data_path": tempfile.mkdtemp(prefix="hardcover-chrome-data-"),
        "cache": tempfile.mkdtemp(prefix="hardcover-chrome-cache-"),
    }
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
    # Reduce bot-detection signals
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])

    chrome_bin = os.getenv("CHROME_BIN")
    if chrome_bin and os.path.exists(chrome_bin):
        options.binary_location = chrome_bin
        logger.debug("Using Chrome binary: %s", chrome_bin)

    return options, headless, runtime_dirs


def chrome_log_path() -> Path:
    """Return the path used for ChromeDriver startup diagnostics."""
    return Path(os.getenv("CHROMEDRIVER_LOG", "/tmp/hardcover-sync-chromedriver.log"))


def create_driver() -> webdriver.Chrome:
    """Return a Chrome WebDriver.

    Respects two optional environment variables:
      CHROME_BIN        – path to the Chromium/Chrome binary (set in Docker)
      CHROMEDRIVER_PATH – path to chromedriver binary (set in Docker)
      CHROMEDRIVER_LOG  – path to ChromeDriver's startup diagnostics

    When those variables are absent (local dev) Selenium's default
    PATH-based discovery is used instead.
    """
    options, headless, runtime_dirs = build_options()
    log_path = chrome_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_sink = open(log_path, "a", encoding="utf-8")

    chromedriver_path = os.getenv("CHROMEDRIVER_PATH")
    try:
        if chromedriver_path and os.path.exists(chromedriver_path):
            logger.debug("Using ChromeDriver: %s", chromedriver_path)
            driver = webdriver.Chrome(
                service=Service(chromedriver_path, log_output=log_sink), options=options
            )
        else:
            driver = webdriver.Chrome(
                service=Service(log_output=log_sink), options=options
            )
    except Exception:
        log_sink.close()
        logger.exception("ChromeDriver failed to start; see %s", log_path)
        for runtime_dir in runtime_dirs.values():
            shutil.rmtree(runtime_dir, ignore_errors=True)
        raise

    original_quit = driver.quit

    def quit_and_cleanup() -> None:
        try:
            original_quit()
        finally:
            log_sink.close()
            for runtime_dir in runtime_dirs.values():
                shutil.rmtree(runtime_dir, ignore_errors=True)

    driver.quit = quit_and_cleanup

    try:
        # Goodreads currently serves an empty document to the default
        # HeadlessChrome user agent. Derive a normal UA from the installed browser
        # so the version remains accurate instead of hard-coding one.
        if headless:
            version = driver.execute_cdp_cmd("Browser.getVersion", {})
            user_agent = browser_user_agent(version.get("userAgent", ""))
            set_user_agent(driver, user_agent)

        if headless:
            # Mask the navigator.webdriver property in headless mode.
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {
                    "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                },
            )
    except Exception:
        driver.quit()
        raise

    return driver
