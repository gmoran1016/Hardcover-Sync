"""Goodreads reading-progress automation via Selenium.

Because Goodreads shut down their public API, progress updates are done
by controlling a headless Chrome browser session.

Authentication strategy
-----------------------
Goodreads uses Amazon's login infrastructure which shows a CAPTCHA
challenge when it detects a headless browser.  To work around this we
use saved session cookies instead of filling the login form.

Run setup_cookies.py ONCE on your local machine to save your session:

    python setup_cookies.py

The cookies are stored in cookies/goodreads.json.  The sync app loads
them on every run.  Re-run setup_cookies.py whenever you are logged out.

Flow for each book:
  1. Load session cookies → navigate to Goodreads (logged in).
  2. Search for the book by title + author.
  3. Navigate to the book page.
  4. If not on Currently Reading shelf, add it.
  5. Click Update progress and submit the page count.
"""

import json
import logging
import os
import re
import time
from urllib.parse import quote_plus

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    NoSuchElementException,
    TimeoutException,
)

from driver import create_driver

logger = logging.getLogger(__name__)
GOODREADS_URL = "https://www.goodreads.com"
COOKIES_FILE = os.path.join(os.path.dirname(__file__), "cookies", "goodreads.json")


class GoodreadsSync:
    """Context-manager that owns a single browser session for Goodreads."""

    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.driver = None

    def __enter__(self):
        self.driver = create_driver()
        return self

    def __exit__(self, *_):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def login(self) -> bool:
        """Authenticate via saved cookies (preferred) or form login (fallback)."""
        if os.path.exists(COOKIES_FILE):
            if self._login_with_cookies():
                return True
            logger.warning(
                "Saved cookies are expired or invalid. "
                "Re-run 'python setup_cookies.py' to refresh them."
            )
            return False

        logger.warning(
            "No cookies file found at %s. "
            "Run 'python setup_cookies.py' to create it. "
            "Falling back to form login (may hit CAPTCHA).",
            COOKIES_FILE,
        )
        return self._login_with_form()

    def update_progress(self, book: dict) -> bool:
        title = book["title"]
        pages = book.get("progress_pages")
        pct = book.get("progress_percent")

        logger.info(
            "Updating Goodreads: '%s' → %s pages / %.1f%%",
            title,
            pages if pages is not None else "?",
            pct or 0.0,
        )

        try:
            book_url = self._search_book(title, book.get("author"))
            if not book_url:
                logger.warning("Could not find '%s' on Goodreads", title)
                return False

            self.driver.get(book_url)
            time.sleep(2)

            self._ensure_currently_reading()
            time.sleep(1)

            return self._do_update_progress(pages, pct)

        except Exception as exc:
            logger.error("Error updating '%s' on Goodreads: %s", title, exc)
            return False

    # ------------------------------------------------------------------
    # Login helpers
    # ------------------------------------------------------------------

    def _login_with_cookies(self) -> bool:
        logger.info("Loading Goodreads session from saved cookies…")
        try:
            with open(COOKIES_FILE) as f:
                cookies = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Could not read cookies file: %s", exc)
            return False

        # Must navigate to the domain before adding cookies
        self.driver.get(GOODREADS_URL)
        time.sleep(1)

        for cookie in cookies:
            cookie.pop("sameSite", None)  # can cause errors in some versions
            try:
                self.driver.add_cookie(cookie)
            except Exception:
                pass

        self.driver.refresh()
        time.sleep(2)

        if self._is_logged_in():
            logger.info("Goodreads authenticated via saved cookies")
            return True

        return False

    def _login_with_form(self) -> bool:
        """Form-based login fallback — may hit Amazon's CAPTCHA in headless mode."""
        logger.info("Attempting Goodreads form login…")
        try:
            self.driver.get(f"{GOODREADS_URL}/user/sign_in")
            wait = WebDriverWait(self.driver, 15)

            # Goodreads shows OAuth buttons first; click "Sign in with email"
            wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    '//button[contains(normalize-space(.), "Sign in with email")]'
                    ' | //a[contains(normalize-space(.), "Sign in with email")]',
                ))
            ).click()
            time.sleep(1)

            wait.until(
                EC.presence_of_element_located((
                    By.CSS_SELECTOR,
                    'input#user_email, input[name="user[email]"], input[type="email"]',
                ))
            ).send_keys(self.email)
            time.sleep(0.4)

            self.driver.find_element(
                By.CSS_SELECTOR,
                'input#user_password, input[name="user[password]"], input[type="password"]',
            ).send_keys(self.password)
            time.sleep(0.4)

            self.driver.find_element(
                By.CSS_SELECTOR, 'input[type="submit"], button[type="submit"]'
            ).click()

            # Wait up to 20s — Amazon CVF/CAPTCHA page will stall here
            for _ in range(20):
                time.sleep(1)
                if self._is_logged_in():
                    logger.info("Goodreads form login successful")
                    return True
                url = self.driver.current_url
                if "cvf" in url or "captcha" in url.lower():
                    logger.error(
                        "Goodreads login blocked by CAPTCHA. "
                        "Run 'python setup_cookies.py' to fix this."
                    )
                    return False

            logger.error("Goodreads form login timed out")
            return False

        except TimeoutException:
            logger.error("Timed out during Goodreads form login")
            return False
        except Exception as exc:
            logger.error("Goodreads form login error: %s", exc)
            return False

    def _is_logged_in(self) -> bool:
        """Return True if the current page indicates an active Goodreads session."""
        url = self.driver.current_url
        title = self.driver.title.lower()
        # Logged-in home page has "recent updates" or user-specific content
        # Sign-in page or Amazon auth pages indicate failure
        if "sign_in" in url or "ap/signin" in url or "ap/cvf" in url:
            return False
        if "sign in" in title and "goodreads" in title:
            return False
        # Confirmed logged-in indicators
        if "recent updates" in title or "goodreads" in url:
            # Check for a nav element only present when logged in
            try:
                self.driver.find_element(
                    By.CSS_SELECTOR,
                    'a[href*="/user/show"], a[href*="/review/list"], nav',
                )
                return True
            except NoSuchElementException:
                pass
        return False

    # ------------------------------------------------------------------
    # Book search & shelf management
    # ------------------------------------------------------------------

    def _search_book(self, title: str, author: str | None) -> str | None:
        query = f"{title} {author}" if author and author not in ("Unknown", "") else title
        self.driver.get(
            f"{GOODREADS_URL}/search?q={quote_plus(query)}&search_type=books"
        )

        try:
            wait = WebDriverWait(self.driver, 10)
            links = wait.until(
                EC.presence_of_all_elements_located((
                    By.CSS_SELECTOR,
                    "a.bookTitle, td.title a, [data-testid='bookTitle']",
                ))
            )

            title_norm = _normalise(title)
            for link in links[:5]:
                text_norm = _normalise(link.text)
                if title_norm in text_norm or text_norm in title_norm:
                    href = link.get_attribute("href")
                    logger.debug("Matched Goodreads result: %s → %s", link.text.strip(), href)
                    return href

            if links:
                href = links[0].get_attribute("href")
                logger.warning(
                    "No exact Goodreads match for '%s'; using first result: %s",
                    title, links[0].text.strip(),
                )
                return href

        except TimeoutException:
            logger.error("Timeout searching Goodreads for '%s'", title)

        return None

    def _ensure_currently_reading(self) -> None:
        """Move book to Currently Reading shelf if it isn't there already."""
        try:
            wait = WebDriverWait(self.driver, 6)
            shelf_btn = wait.until(
                EC.presence_of_element_located((
                    By.CSS_SELECTOR,
                    "button.wtrButton, .wantToReadButton, "
                    "button[data-testid='bookPagePrimaryActionButton'], "
                    "button.Button--secondary.Button--block",
                ))
            )

            if "currently reading" in shelf_btn.text.lower():
                return

            # Open the shelf dropdown and pick "Currently reading"
            for caret_sel in (
                ".wtrButtonDesktop .caretButton",
                "button.caretButton",
                ".dropdownButton",
            ):
                try:
                    self.driver.find_element(By.CSS_SELECTOR, caret_sel).click()
                    time.sleep(0.5)
                    WebDriverWait(self.driver, 5).until(
                        EC.element_to_be_clickable((
                            By.XPATH,
                            '//*[normalize-space(text())="Currently reading" '
                            'or normalize-space(text())="Currently Reading"]',
                        ))
                    ).click()
                    time.sleep(1)
                    logger.info("Added book to Currently Reading shelf")
                    return
                except (NoSuchElementException, TimeoutException):
                    continue

        except TimeoutException:
            pass
        except Exception as exc:
            logger.debug("_ensure_currently_reading: %s", exc)

    # ------------------------------------------------------------------
    # Progress update
    # ------------------------------------------------------------------

    def _do_update_progress(self, pages: int | None, pct: float | None) -> bool:
        """Click 'Update progress', fill in the form, submit."""
        triggers = [
            (By.XPATH, '//a[contains(normalize-space(.), "Update progress")]'),
            (By.XPATH, '//button[contains(normalize-space(.), "Update progress")]'),
            (By.CSS_SELECTOR, "a.updateButton, .updateProgressButton"),
            (By.XPATH, '//a[contains(@href, "user_status")]'),
        ]
        for by, sel in triggers:
            try:
                el = self.driver.find_element(by, sel)
                if el.is_displayed():
                    self.driver.execute_script("arguments[0].scrollIntoView(true);", el)
                    time.sleep(0.3)
                    el.click()
                    logger.debug("Clicked update-progress trigger via: %s", sel)
                    break
            except (NoSuchElementException, ElementClickInterceptedException):
                continue

        time.sleep(1)

        if pages is not None:
            for sel in [
                'input[name="user_status[page]"]',
                'input[name*="page"]',
                "input#current_page",
                "input.pageInput",
                'input[placeholder*="page" i]',
                "input[type='number']",
            ]:
                try:
                    inp = self.driver.find_element(By.CSS_SELECTOR, sel)
                    if inp.is_displayed():
                        inp.clear()
                        inp.send_keys(str(pages))
                        if self._submit_progress_form():
                            logger.info("Goodreads progress saved: %d pages", pages)
                            return True
                except NoSuchElementException:
                    continue

        if pct is not None:
            for sel in [
                'input[name="user_status[percent]"]',
                'input[name*="percent"]',
                "input#current_percent",
                'input[placeholder*="percent" i]',
            ]:
                try:
                    inp = self.driver.find_element(By.CSS_SELECTOR, sel)
                    if inp.is_displayed():
                        inp.clear()
                        inp.send_keys(str(int(pct)))
                        if self._submit_progress_form():
                            logger.info("Goodreads progress saved: %.1f%%", pct)
                            return True
                except NoSuchElementException:
                    continue

        logger.error("Could not locate a progress input field on Goodreads")
        return False

    def _submit_progress_form(self) -> bool:
        for sel in [
            'input[type="submit"][value*="Save" i]',
            'input[type="submit"][value*="Update" i]',
            "input[type='submit']",
            "button[type='submit']",
            'button[data-testid="saveProgressButton"]',
        ]:
            try:
                btn = self.driver.find_element(By.CSS_SELECTOR, sel)
                if btn.is_displayed():
                    btn.click()
                    time.sleep(1)
                    return True
            except NoSuchElementException:
                continue
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"\s*\(.*?\)", "", text)
    return text.strip()
