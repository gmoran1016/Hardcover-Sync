"""Shared Selenium WebDriver factory used by both Goodreads and StoryGraph modules."""

import os
import logging
import subprocess

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

# Suppress Selenium's built-in Plausible telemetry
os.environ.setdefault("SE_AVOID_STATS", "true")

logger = logging.getLogger(__name__)


def browser_user_agent(user_agent: str) -> str:
    """Return the installed browser's normal UA without the headless marker."""
    return user_agent.replace("HeadlessChrome/", "Chrome/")


def create_driver() -> webdriver.Chrome:
    """Return a headless Chrome WebDriver.

    Respects two optional environment variables:
      CHROME_BIN        – path to the Chromium/Chrome binary (set in Docker)
      CHROMEDRIVER_PATH – path to chromedriver binary (set in Docker)

    When those variables are absent (local dev) Selenium's default
    PATH-based discovery is used instead.
    """
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-extensions")
    options.add_argument("--log-level=3")          # Silence Chrome's stderr noise
    # Keep Chrome's normal multi-process model; single-process is crash-prone.
    options.add_argument("--disable-setuid-sandbox")
    options.add_argument("--memory-pressure-off")
    options.add_argument("--max_old_space_size=512")
    options.add_argument("--disk-cache-size=1")
    options.add_argument("--media-cache-size=1")
    options.add_argument("--disable-application-cache")
    options.add_argument("--aggressive-cache-discard")
    # Reduce bot-detection signals
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    chrome_bin = os.getenv("CHROME_BIN")
    if chrome_bin and os.path.exists(chrome_bin):
        options.binary_location = chrome_bin
        logger.debug("Using Chrome binary: %s", chrome_bin)

    # Route chromedriver's own log to devnull to suppress DevTools/USB lines
    log_sink = subprocess.DEVNULL

    chromedriver_path = os.getenv("CHROMEDRIVER_PATH")
    if chromedriver_path and os.path.exists(chromedriver_path):
        logger.debug("Using ChromeDriver: %s", chromedriver_path)
        driver = webdriver.Chrome(
            service=Service(chromedriver_path, log_output=log_sink), options=options
        )
    else:
        driver = webdriver.Chrome(
            service=Service(log_output=log_sink), options=options
        )

    # Goodreads currently serves an empty document to the default
    # HeadlessChrome user agent. Derive a normal UA from the installed browser
    # so the version remains accurate instead of hard-coding one.
    version = driver.execute_cdp_cmd("Browser.getVersion", {})
    user_agent = browser_user_agent(version.get("userAgent", ""))
    if user_agent:
        driver.execute_cdp_cmd(
            "Network.setUserAgentOverride",
            {"userAgent": user_agent},
        )

    # Mask the navigator.webdriver property to reduce bot-detection.
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
    )

    return driver
