"""Goodreads reading-progress automation via Selenium.

Because Goodreads shut down their public API, progress updates are done
by controlling a headless Chrome browser session.

Flow for each book:
  1. Search Goodreads for the book title (+ author).
  2. Navigate to the book page.
  3. If the book is not already on the "Currently Reading" shelf, add it.
  4. Click the "Update progress" control and submit the page count.
"""

import logging
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
        logger.info("Logging in to Goodreads…")
        try:
            self.driver.get(f"{GOODREADS_URL}/user/sign_in")
            wait = WebDriverWait(self.driver, 15)

            email_field = wait.until(
                EC.presence_of_element_located((
                    By.CSS_SELECTOR,
                    'input#user_email, input[name="user[email]"], input[type="email"]',
                ))
            )
            email_field.clear()
            email_field.send_keys(self.email)
            time.sleep(0.4)

            pw_field = self.driver.find_element(
                By.CSS_SELECTOR,
                'input#user_password, input[name="user[password]"], input[type="password"]',
            )
            pw_field.clear()
            pw_field.send_keys(self.password)
            time.sleep(0.4)

            self.driver.find_element(
                By.CSS_SELECTOR, 'input[type="submit"], button[type="submit"]'
            ).click()

            # Wait until we leave the sign-in page
            WebDriverWait(self.driver, 15).until(
                lambda d: "sign_in" not in d.current_url
            )

            if "goodreads.com" in self.driver.current_url:
                logger.info("Goodreads login successful")
                return True

            logger.error("Goodreads login may have failed. URL: %s", self.driver.current_url)
            return False

        except TimeoutException:
            logger.error("Timed out waiting for Goodreads login")
            return False
        except Exception as exc:
            logger.error("Goodreads login error: %s", exc)
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
    # Internal helpers
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

            # No close match — fall back to first result
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
        """If the book is not on the Currently Reading shelf, put it there."""
        try:
            wait = WebDriverWait(self.driver, 6)
            # The shelf button text reflects the current shelf assignment
            shelf_btn = wait.until(
                EC.presence_of_element_located((
                    By.CSS_SELECTOR,
                    "button.wtrButton, .wantToReadButton, "
                    "button[data-testid='bookPagePrimaryActionButton']",
                ))
            )

            if "currently reading" in shelf_btn.text.lower():
                return  # Already correct

            # Try opening the shelf dropdown and picking "Currently reading"
            for caret_sel in (
                ".wtrButtonDesktop .caretButton",
                "button.caretButton",
                ".dropdownButton",
            ):
                try:
                    caret = self.driver.find_element(By.CSS_SELECTOR, caret_sel)
                    caret.click()
                    time.sleep(0.5)
                    option = WebDriverWait(self.driver, 5).until(
                        EC.element_to_be_clickable((
                            By.XPATH,
                            '//*[normalize-space(text())="Currently reading" '
                            'or normalize-space(text())="Currently Reading"]',
                        ))
                    )
                    option.click()
                    time.sleep(1)
                    logger.info("Added book to Currently Reading shelf")
                    return
                except (NoSuchElementException, TimeoutException):
                    continue

        except TimeoutException:
            pass  # Button not found; may already be set correctly
        except Exception as exc:
            logger.debug("_ensure_currently_reading: %s", exc)

    def _do_update_progress(self, pages: int | None, pct: float | None) -> bool:
        """Click 'Update progress', fill in the form, submit."""
        # --- Locate and click the update-progress trigger ---
        trigger_found = False
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
                    trigger_found = True
                    logger.debug("Clicked update-progress trigger via: %s", sel)
                    break
            except (NoSuchElementException, ElementClickInterceptedException):
                continue

        if not trigger_found:
            logger.debug("No 'Update progress' trigger found; attempting to fill form directly")

        time.sleep(1)

        # --- Try to enter page number ---
        if pages is not None:
            page_selectors = [
                'input[name="user_status[page]"]',
                'input[name*="page"]',
                "input#current_page",
                "input.pageInput",
                'input[placeholder*="page" i]',
                "input[type='number']",
            ]
            for sel in page_selectors:
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

        # --- Fallback: enter percentage ---
        if pct is not None:
            pct_selectors = [
                'input[name="user_status[percent]"]',
                'input[name*="percent"]',
                "input#current_percent",
                'input[placeholder*="percent" i]',
            ]
            for sel in pct_selectors:
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
        submit_selectors = [
            'input[type="submit"][value*="Save" i]',
            'input[type="submit"][value*="Update" i]',
            "input[type='submit']",
            "button[type='submit']",
            'button[data-testid="saveProgressButton"]',
        ]
        for sel in submit_selectors:
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
    """Lowercase, strip series parentheticals, collapse whitespace."""
    text = (text or "").lower()
    text = re.sub(r"\s*\(.*?\)", "", text)
    return text.strip()
