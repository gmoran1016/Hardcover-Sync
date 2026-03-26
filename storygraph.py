"""StoryGraph reading-progress automation via Selenium.

StoryGraph has no public API, so progress is updated through a
headless browser session.

Flow for each book:
  1. Search StoryGraph for the book title.
  2. Navigate to the book page.
  3. Find the reading-progress widget and submit updated pages/percent.
"""

import logging
import re
import time
from urllib.parse import quote_plus

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException

from driver import create_driver

logger = logging.getLogger(__name__)
STORYGRAPH_URL = "https://app.thestorygraph.com"


class StorygraphSync:
    """Context-manager that owns a single browser session for StoryGraph."""

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
        logger.info("Logging in to StoryGraph…")
        try:
            self.driver.get(f"{STORYGRAPH_URL}/users/sign_in")
            wait = WebDriverWait(self.driver, 15)

            # Confirmed selectors from live page inspection: #user_email / #user_password
            email_field = wait.until(
                EC.presence_of_element_located((
                    By.CSS_SELECTOR,
                    '#user_email, input[name="user[email]"], input[type="email"]',
                ))
            )
            email_field.clear()
            email_field.send_keys(self.email)
            time.sleep(0.4)

            pw_field = self.driver.find_element(
                By.CSS_SELECTOR,
                '#user_password, input[name="user[password]"], input[type="password"]',
            )
            pw_field.clear()
            pw_field.send_keys(self.password)
            time.sleep(0.4)

            # StoryGraph uses a "Sign in" button (text-based)
            try:
                btn = self.driver.find_element(
                    By.XPATH,
                    '//button[contains(normalize-space(.), "Sign in") '
                    'or contains(normalize-space(.), "Sign In")]',
                )
                btn.click()
            except NoSuchElementException:
                self.driver.find_element(
                    By.CSS_SELECTOR, 'input[type="submit"], button[type="submit"]'
                ).click()

            WebDriverWait(self.driver, 15).until(
                lambda d: "sign_in" not in d.current_url
            )

            if STORYGRAPH_URL in self.driver.current_url:
                logger.info("StoryGraph login successful")
                return True

            logger.error("StoryGraph login failed. URL: %s", self.driver.current_url)
            return False

        except TimeoutException:
            logger.error("Timeout during StoryGraph login")
            return False
        except Exception as exc:
            logger.error("StoryGraph login error: %s", exc)
            return False

    def update_progress(self, book: dict) -> bool:
        title = book["title"]
        pages = book.get("progress_pages")
        pct = book.get("progress_percent")
        total = book.get("total_pages")

        logger.info(
            "Updating StoryGraph: '%s' → %s pages / %.1f%%",
            title,
            pages if pages is not None else "?",
            pct or 0.0,
        )

        try:
            book_url = self._search_book(title)
            if not book_url:
                logger.warning("Could not find '%s' on StoryGraph", title)
                return False

            self.driver.get(book_url)
            time.sleep(2)

            return self._do_update_progress(pages, pct, total)

        except Exception as exc:
            logger.error("Error updating StoryGraph for '%s': %s", title, exc)
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _search_book(self, title: str) -> str | None:
        search_url = f"{STORYGRAPH_URL}/browse?search_term={quote_plus(title)}"
        self.driver.get(search_url)

        try:
            wait = WebDriverWait(self.driver, 10)
            # StoryGraph search result containers
            results = wait.until(
                EC.presence_of_all_elements_located((
                    By.CSS_SELECTOR,
                    ".book-title-author-and-series, h3.font-semibold, .book-title",
                ))
            )

            title_norm = _normalise(title)
            for result in results[:5]:
                text_norm = _normalise(result.text)
                if title_norm in text_norm or text_norm in title_norm:
                    href = self._extract_href(result)
                    if href:
                        logger.debug("Matched StoryGraph result: %s", result.text.strip())
                        return href

            # Fall back to first result
            if results:
                href = self._extract_href(results[0])
                if href:
                    logger.warning(
                        "No exact StoryGraph match for '%s'; using first result: %s",
                        title, results[0].text.strip(),
                    )
                    return href

        except TimeoutException:
            logger.error("Timeout searching StoryGraph for '%s'", title)

        return None

    def _extract_href(self, element) -> str | None:
        """Walk up the DOM to find an ancestor <a> tag with a href."""
        try:
            link = element.find_element(By.XPATH, "./ancestor-or-self::a[@href]")
            return link.get_attribute("href")
        except NoSuchElementException:
            pass
        try:
            link = element.find_element(By.CSS_SELECTOR, "a[href]")
            return link.get_attribute("href")
        except NoSuchElementException:
            return None

    def _do_update_progress(
        self, pages: int | None, pct: float | None, total_pages: int | None
    ) -> bool:
        """Open the progress widget on a StoryGraph book page and submit progress."""

        # --- Try to open a progress/reading-status dropdown or form ---
        trigger_selectors = [
            (By.XPATH, '//button[contains(normalize-space(.), "Update progress")]'),
            (By.XPATH, '//button[contains(normalize-space(.), "Log progress")]'),
            (By.XPATH, '//a[contains(normalize-space(.), "Update progress")]'),
            (By.CSS_SELECTOR, "button.expand-dropdown-button"),
            # "Reading" status buttons that expand a journal entry form
            (By.XPATH, '//button[contains(normalize-space(.), "currently reading") '
                        'or contains(normalize-space(.), "Currently Reading")]'),
        ]

        for by, sel in trigger_selectors:
            try:
                el = self.driver.find_element(by, sel)
                if el.is_displayed():
                    self.driver.execute_script("arguments[0].scrollIntoView(true);", el)
                    el.click()
                    time.sleep(1)
                    logger.debug("Opened StoryGraph progress widget via: %s", sel)
                    break
            except NoSuchElementException:
                continue

        # --- Fill in the progress form ---
        # StoryGraph typically offers a pages or percent field
        if pages is not None:
            page_selectors = [
                'input[name*="page"]',
                'input[id*="page"]',
                'input[placeholder*="page" i]',
                "input[type='number']",
            ]
            for sel in page_selectors:
                try:
                    inp = self.driver.find_element(By.CSS_SELECTOR, sel)
                    if inp.is_displayed():
                        inp.clear()
                        inp.send_keys(str(pages))
                        if self._submit_form():
                            logger.info("StoryGraph progress saved: %d pages", pages)
                            return True
                except NoSuchElementException:
                    continue

        # Fallback: percentage
        if pct is not None:
            pct_selectors = [
                'input[name*="percent"]',
                'input[id*="percent"]',
                'input[placeholder*="percent" i]',
            ]
            for sel in pct_selectors:
                try:
                    inp = self.driver.find_element(By.CSS_SELECTOR, sel)
                    if inp.is_displayed():
                        inp.clear()
                        inp.send_keys(str(int(pct)))
                        if self._submit_form():
                            logger.info("StoryGraph progress saved: %.1f%%", pct)
                            return True
                except NoSuchElementException:
                    continue

        logger.warning("Could not locate a progress input field on StoryGraph")
        return False

    def _submit_form(self) -> bool:
        submit_selectors = [
            'input[type="submit"][value="Update"]',
            'input[type="submit"][value*="Save" i]',
            "input[type='submit']",
            "button[type='submit']",
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
    text = (text or "").lower()
    text = re.sub(r"\s*\(.*?\)", "", text)
    return text.strip()
