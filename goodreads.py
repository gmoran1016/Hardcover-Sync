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

    def mark_finished(self, book: dict) -> bool:
        """Mark a book as finished on Goodreads via the 'I'm finished!' button."""
        title = book["title"]
        logger.info("Marking Goodreads as finished: '%s'", title)
        try:
            self.driver.get(GOODREADS_URL)
            time.sleep(2)

            if not self._click_update_progress_for(title):
                logger.warning("'%s' not in Currently Reading widget; can't mark finished", title)
                return False

            time.sleep(1)
            wait = WebDriverWait(self.driver, 8)
            finished_btn = wait.until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    '//button[contains(normalize-space(.), "finished")]',
                ))
            )
            finished_btn.click()
            time.sleep(2)
            logger.info("Goodreads: marked '%s' as finished", title)
            return True

        except TimeoutException:
            logger.error("Timed out finding 'I'm finished!' button for '%s'", title)
            return False
        except Exception as exc:
            logger.error("Error marking '%s' as finished on Goodreads: %s", title, exc)
            return False

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
            # The "Update progress" button lives on the home page's Currently Reading widget,
            # not on the individual book page.
            self.driver.get(GOODREADS_URL)
            time.sleep(2)

            if not self._click_update_progress_for(title):
                # Book isn't in Currently Reading widget — add it first via the book page
                logger.info("Book not in Currently Reading widget; adding it to the shelf…")
                book_url = self._search_book(title, book.get("author"))
                if book_url:
                    self.driver.get(book_url)
                    time.sleep(2)
                    self._ensure_currently_reading()
                    time.sleep(2)
                    self.driver.get(GOODREADS_URL)
                    time.sleep(2)
                    if not self._click_update_progress_for(title):
                        logger.error("Still can't find Update progress for '%s'", title)
                        return False

            time.sleep(1)
            return self._fill_home_page_progress_form(pages, pct)

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
            except Exception as exc:
                logger.debug("Skipped cookie '%s': %s", cookie.get("name"), exc)

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
        """Click the shelf button and pick 'Currently reading' from the dropdown."""
        try:
            wait = WebDriverWait(self.driver, 6)
            shelf_btn = wait.until(
                EC.presence_of_element_located((
                    By.CSS_SELECTOR,
                    "button.Button--secondary.Button--block",
                ))
            )
            if "currently reading" in shelf_btn.text.lower():
                return

            shelf_btn.click()
            time.sleep(0.5)
            WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((
                    By.XPATH,
                    '//button[normalize-space(.)="Currently reading"]',
                ))
            ).click()
            time.sleep(1)
            logger.info("Added book to Currently Reading shelf")

        except (TimeoutException, NoSuchElementException):
            pass
        except Exception as exc:
            logger.debug("_ensure_currently_reading: %s", exc)

    # ------------------------------------------------------------------
    # Home-page progress update (the only place Goodreads shows the form)
    # ------------------------------------------------------------------

    def _click_update_progress_for(self, title: str) -> bool:
        """Find and click 'Update progress' for the given book in the Currently Reading widget."""
        title_norm = _normalise(title)

        try:
            wait = WebDriverWait(self.driver, 5)
            btns = wait.until(
                EC.presence_of_all_elements_located((
                    By.XPATH, '//button[normalize-space(.)="Update progress"]'
                ))
            )
        except TimeoutException:
            logger.debug("No 'Update progress' buttons found on home page")
            return False

        def js_click(el):
            # Use JS click to bypass any overlapping banner/header elements
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.3)
            self.driver.execute_script("arguments[0].click();", el)

        if len(btns) == 1:
            js_click(btns[0])
            logger.debug("Clicked Update progress (only one button)")
            return True

        # Multiple books — find the one whose container mentions this title
        for btn in btns:
            try:
                container = btn.find_element(
                    By.XPATH,
                    "./ancestor::div[.//a[contains(@href,'/book/show/')]][1]",
                )
                if title_norm[:15] in _normalise(container.text):
                    js_click(btn)
                    logger.debug("Clicked Update progress for '%s'", title)
                    return True
            except NoSuchElementException:
                continue

        # Fallback: first button
        if btns:
            js_click(btns[0])
            logger.warning("Could not match book; clicking first 'Update progress' button")
            return True

        return False

    def _fill_home_page_progress_form(self, pages: int | None, pct: float | None) -> bool:
        """Fill and submit the inline progress form that appears after clicking Update progress."""
        try:
            wait = WebDriverWait(self.driver, 8)

            if pages is not None:
                # Ensure we're in pages mode (the # button)
                try:
                    hash_btn = self.driver.find_element(
                        By.XPATH, '//button[normalize-space(.)="#"]'
                    )
                    if hash_btn.is_displayed():
                        hash_btn.click()
                        time.sleep(0.3)
                except NoSuchElementException:
                    pass

                # The page input has placeholder "p. NNN"
                page_input = wait.until(
                    EC.presence_of_element_located((
                        By.XPATH, '//input[starts-with(@placeholder,"p.")]'
                    ))
                )
                page_input.clear()
                page_input.send_keys(str(pages))

            elif pct is not None:
                # Switch to percent mode
                try:
                    pct_btn = self.driver.find_element(
                        By.XPATH, '//button[normalize-space(.)="%"]'
                    )
                    if pct_btn.is_displayed():
                        pct_btn.click()
                        time.sleep(0.3)
                except NoSuchElementException:
                    pass

                pct_input = wait.until(
                    EC.presence_of_element_located((
                        By.XPATH, '//input[starts-with(@placeholder,"%")]'
                    ))
                )
                pct_input.clear()
                pct_input.send_keys(str(int(pct)))

            else:
                logger.error("No pages or percent to submit")
                return False

            # The submit button is also labelled "Update progress"
            # After the form opens there should be exactly one visible
            submit = wait.until(
                EC.element_to_be_clickable((
                    By.XPATH, '//button[normalize-space(.)="Update progress"]'
                ))
            )
            submit.click()
            time.sleep(1)

            if pages is not None:
                logger.info("Goodreads progress saved: %d pages", pages)
            else:
                logger.info("Goodreads progress saved: %.1f%%", pct)
            return True

        except TimeoutException:
            logger.error("Timed out waiting for progress form fields")
            return False
        except Exception as exc:
            logger.error("Error filling Goodreads progress form: %s", exc)
            return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"\s*\(.*?\)", "", text)
    return text.strip()
