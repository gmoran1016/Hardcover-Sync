"""StoryGraph reading-progress automation via Selenium.

StoryGraph has no public API, so progress is updated through a
headless browser session.

Authentication: tries saved cookies first (cookies/storygraph.json),
falls back to form login.  Run setup_cookies.py to create/refresh cookies.

Flow for each book:
  1. Authenticate (cookies or form).
  2. Search StoryGraph for the book title.
  3. Navigate to the book page.
  4. Find the reading-progress widget and submit updated pages/percent.
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
from selenium.common.exceptions import NoSuchElementException, TimeoutException

from driver import create_driver

logger = logging.getLogger(__name__)
STORYGRAPH_URL = "https://app.thestorygraph.com"
COOKIES_FILE = os.path.join(os.path.dirname(__file__), "cookies", "storygraph.json")


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
        """Authenticate via saved cookies (preferred) or form login (fallback)."""
        if os.path.exists(COOKIES_FILE):
            if self._login_with_cookies():
                return True
            logger.warning(
                "StoryGraph cookies expired. Re-run 'python setup_cookies.py'."
            )
            # Fall through to form login
        return self._login_with_form()

    def _login_with_cookies(self) -> bool:
        logger.info("Loading StoryGraph session from saved cookies…")
        try:
            with open(COOKIES_FILE) as f:
                cookies = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Could not read StoryGraph cookies: %s", exc)
            return False

        self.driver.get(STORYGRAPH_URL)
        time.sleep(1)
        for cookie in cookies:
            cookie.pop("sameSite", None)
            try:
                self.driver.add_cookie(cookie)
            except Exception as exc:
                logger.debug("Skipped cookie '%s': %s", cookie.get("name"), exc)
        self.driver.refresh()
        time.sleep(2)

        if "sign_in" not in self.driver.current_url and STORYGRAPH_URL in self.driver.current_url:
            logger.info("StoryGraph authenticated via saved cookies")
            return True
        return False

    def _login_with_form(self) -> bool:
        logger.info("Logging in to StoryGraph via form…")
        try:
            self.driver.get(f"{STORYGRAPH_URL}/users/sign_in")
            wait = WebDriverWait(self.driver, 15)

            # Confirmed selectors from live page inspection
            wait.until(
                EC.presence_of_element_located((
                    By.CSS_SELECTOR,
                    '#user_email, input[name="user[email]"], input[type="email"]',
                ))
            ).send_keys(self.email)
            time.sleep(0.4)

            self.driver.find_element(
                By.CSS_SELECTOR,
                '#user_password, input[name="user[password]"], input[type="password"]',
            ).send_keys(self.password)
            time.sleep(0.4)

            try:
                self.driver.find_element(
                    By.XPATH,
                    '//button[contains(normalize-space(.), "Sign in") '
                    'or contains(normalize-space(.), "Sign In")]',
                ).click()
            except NoSuchElementException:
                self.driver.find_element(
                    By.CSS_SELECTOR, 'input[type="submit"], button[type="submit"]'
                ).click()

            WebDriverWait(self.driver, 15).until(
                lambda d: "sign_in" not in d.current_url
            )

            if STORYGRAPH_URL in self.driver.current_url:
                logger.info("StoryGraph form login successful")
                return True

            logger.error("StoryGraph login failed. URL: %s", self.driver.current_url)
            return False

        except TimeoutException:
            logger.error("Timeout during StoryGraph login")
            return False
        except Exception as exc:
            logger.error("StoryGraph login error: %s", exc)
            return False

    def mark_finished(self, book: dict) -> bool:
        """Mark a book as finished on StoryGraph via the 'mark as finished' button."""
        title = book["title"]
        logger.info("Marking StoryGraph as finished: '%s'", title)
        try:
            book_url = self._search_book(title)
            if not book_url:
                logger.warning("Could not find '%s' on StoryGraph to mark finished", title)
                return False

            self.driver.get(book_url)
            time.sleep(2)

            # Dismiss banners
            for btn in self.driver.find_elements(
                By.XPATH, '//button[normalize-space(.)="Dismiss"]'
            ):
                if btn.is_displayed():
                    self.driver.execute_script("arguments[0].click();", btn)
                    time.sleep(0.5)

            wait = WebDriverWait(self.driver, 8)

            # The finish button is inside the reading-status dropdown — expand it first.
            # Use the displayed one (there's also a hidden mobile duplicate at y=0).
            expand_btns = self.driver.find_elements(By.CSS_SELECTOR, ".expand-dropdown-button")
            expand_btn = next((b for b in expand_btns if b.is_displayed()), None)
            if expand_btn:
                self.driver.execute_script("arguments[0].scrollIntoView(true);", expand_btn)
                self.driver.execute_script("arguments[0].click();", expand_btn)
                time.sleep(0.5)

            wait.until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".mark-as-finished-btn"))
            )
            all_finished = self.driver.find_elements(By.CSS_SELECTOR, ".mark-as-finished-btn")
            finished_btn = next((b for b in all_finished if b.is_displayed()), None)
            if finished_btn is None:
                logger.error("mark-as-finished button not visible for '%s'", title)
                return False
            self.driver.execute_script("arguments[0].click();", finished_btn)
            time.sleep(2)
            logger.info("StoryGraph: marked '%s' as finished", title)
            return True

        except TimeoutException:
            logger.error("Timed out finding 'mark as finished' button for '%s'", title)
            return False
        except Exception as exc:
            logger.error("Error marking '%s' as finished on StoryGraph: %s", title, exc)
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
            time.sleep(4)

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
        """Find the book page URL from a search result element.

        StoryGraph search results wrap the title in a series link (/series/...),
        so we must look for a /books/<uuid> link inside the containing .book-pane
        card rather than just walking up to the nearest ancestor <a>.
        """
        try:
            # Walk up to the book-pane card, then find the first /books/<uuid> link
            card = element.find_element(
                By.XPATH, "./ancestor::div[contains(@class,'book-pane')][1]"
            )
            links = card.find_elements(By.CSS_SELECTOR, "a[href*='/books/']")
            for link in links:
                path = link.get_attribute("href") or ""
                # Skip /books/new and /books/.../editions
                if "/books/new" in path or "/editions" in path:
                    continue
                if "/books/" in path:
                    return path
        except NoSuchElementException:
            pass

        # Fallback: any ancestor <a> with /books/ in href
        try:
            link = element.find_element(
                By.XPATH, "./ancestor-or-self::a[contains(@href,'/books/')]"
            )
            return link.get_attribute("href")
        except NoSuchElementException:
            return None

    def _ensure_currently_reading(self) -> bool:
        """Click the 'currently reading' shelf button on the current StoryGraph book page."""
        try:
            # Wait for the expand button to be present, then JS-click it to open the dropdown.
            # The read-status-button items are always in the DOM but inside a hidden parent;
            # is_displayed() returns False for them even after the dropdown opens, so we
            # use JS clicks throughout to bypass visibility checks.
            try:
                WebDriverWait(self.driver, 8).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".expand-dropdown-button"))
                )
                clicked = self.driver.execute_script("""
                    var btns = document.querySelectorAll('.expand-dropdown-button');
                    for (var b of btns) {
                        if (b.offsetParent !== null) { b.click(); return true; }
                    }
                    // Fall back: click the first one regardless
                    if (btns.length > 0) { btns[0].click(); return true; }
                    return false;
                """)
                logger.debug("StoryGraph expand-dropdown-button clicked: %s", clicked)
                time.sleep(1)
            except TimeoutException:
                logger.warning("StoryGraph expand-dropdown-button not found within 8s")

            # JS-click the 'currently reading' button regardless of visibility.
            # Try the full phrase first, then fall back to just "reading".
            # If NO "currently reading" option exists, the book is already on that
            # shelf (the current status is excluded from the change options), so
            # return a special sentinel so the caller knows to skip shelving.
            result = self.driver.execute_script("""
                var btns = document.querySelectorAll('button.read-status-button');
                var texts = [];
                for (var b of btns) {
                    var t = b.textContent.toLowerCase().trim();
                    texts.push(t);
                    if (t.includes('currently reading') || t === 'reading') {
                        b.click();
                        return 'clicked:' + t;
                    }
                }
                if (btns.length > 0) {
                    return 'already-reading | texts: [' + texts.join(' | ') + ']';
                }
                return 'not found | texts: [' + texts.join(' | ') + ']';
            """)
            logger.debug("StoryGraph read-status-button JS result: %s", result)

            if result.startswith("already-reading"):
                logger.info("Book already on Currently Reading shelf on StoryGraph (no shelf change needed)")
                return True

            if not result.startswith("clicked"):
                logger.warning("'Currently reading' shelf button not found on StoryGraph page: %s", result)
                return False

            time.sleep(2)
            logger.info("Added book to Currently Reading on StoryGraph")
            return True

        except Exception as exc:
            logger.warning("_ensure_currently_reading (StoryGraph): %s", exc)
            return False

    def _find_track_progress_button(self):
        """Return the track-progress button element, or None if not found.

        Tries several selectors in order because StoryGraph has renamed this
        button at least once (edit-progress → track-progress). Also falls back
        to text-content matching in case the class changes again.
        """
        result = self.driver.execute_script("""
            var selectors = [
                'button.edit-progress',
                'button.track-progress-button',
                'button.edit-progress-button',
            ];
            for (var sel of selectors) {
                var btns = document.querySelectorAll(sel);
                if (btns.length > 0) return btns[0];
            }
            // Text-content fallback
            var all = document.querySelectorAll('button');
            for (var b of all) {
                var t = b.textContent.toLowerCase().trim();
                if (t.includes('edit') && t.includes('progress')) return b;
                if (t.includes('track') && t.includes('progress')) return b;
                if (t === 'update progress' || t === 'log progress') return b;
            }
            // Diagnostic: log unique button classes for debugging
            var classes = Array.from(new Set(
                Array.from(document.querySelectorAll('button[class]')).map(function(b){ return b.className; })
            )).join(' | ');
            console.log('[hardcover-sync] no progress button found. button classes: ' + classes);
            return null;
        """)
        if result is None:
            logger.warning(
                "Track progress button not found — check browser console logs for button classes"
            )
        return result

    def _do_update_progress(
        self, pages: int | None, pct: float | None, total_pages: int | None
    ) -> bool:
        """Open the inline progress form and submit updated page count."""
        try:
            wait = WebDriverWait(self.driver, 8)

            # Dismiss any notification banners
            try:
                for btn in self.driver.find_elements(
                    By.XPATH, '//button[normalize-space(.)="Dismiss"]'
                ):
                    if btn.is_displayed():
                        self.driver.execute_script("arguments[0].click();", btn)
                        time.sleep(0.5)
                        logger.debug("Dismissed StoryGraph notification banner")
            except Exception:
                pass

            # The "Track progress" button is hidden until the expand-dropdown-button is clicked.
            # Click expand first, then check for track-progress-button. If absent, the book
            # isn't on the Currently Reading shelf yet — add it, then repeat.
            self.driver.execute_script("""
                var btns = document.querySelectorAll('.expand-dropdown-button');
                for (var b of btns) { if (b.offsetParent !== null) { b.click(); return; } }
                if (btns.length > 0) btns[0].click();
            """)
            time.sleep(1)

            track_btn = self._find_track_progress_button()
            if not track_btn:
                if not self._ensure_currently_reading():
                    logger.error("Could not move book to Currently Reading on StoryGraph")
                    return False
                # After shelving, expand again and wait for track-progress-button
                time.sleep(3)
                self.driver.execute_script("""
                    var btns = document.querySelectorAll('.expand-dropdown-button');
                    for (var b of btns) { if (b.offsetParent !== null) { b.click(); return; } }
                    if (btns.length > 0) btns[0].click();
                """)
                time.sleep(1)
                track_btn = self._find_track_progress_button()
                if not track_btn:
                    logger.error("Track progress button never appeared after shelving")
                    return False

            # JS-click the track progress button to open the inline form
            clicked = self.driver.execute_script("""
                var btn = arguments[0];
                btn.scrollIntoView(true);
                btn.click();
                return btn.offsetParent !== null ? 'visible' : 'hidden';
            """, track_btn)
            logger.debug("track-progress-button click result: %s", clicked)
            time.sleep(1)

            value_to_set = None
            if pages is not None:
                value_to_set = str(pages)
            elif pct is not None and total_pages:
                value_to_set = str(int(total_pages * pct / 100))
            else:
                logger.warning("No pages or percent available for StoryGraph update")
                return False

            # Wait for the progress input, then set value + fire events via JS so
            # Stimulus/Rails UJS picks up the change regardless of visibility state.
            try:
                wait.until(
                    EC.presence_of_element_located((
                        By.CSS_SELECTOR, "input#read_status_progress_number",
                    ))
                )
            except TimeoutException:
                logger.error("Progress input never appeared after clicking track-progress-button")
                return False

            result = self.driver.execute_script("""
                var inputs = document.querySelectorAll('input#read_status_progress_number');
                var inp = null;
                for (var i of inputs) {
                    if (i.offsetParent !== null) { inp = i; break; }
                }
                if (!inp && inputs.length > 0) inp = inputs[0];
                if (!inp) return 'no input';
                inp.focus();
                inp.value = arguments[0];
                inp.dispatchEvent(new Event('input', {bubbles: true}));
                inp.dispatchEvent(new Event('change', {bubbles: true}));
                return 'set:' + inp.value;
            """, value_to_set)
            logger.debug("Progress input JS result: %s", result)
            if not result or not result.startswith("set:"):
                logger.error("Could not set progress input value: %s", result)
                return False

            # Click the Save button via JS
            save_result = self.driver.execute_script("""
                var btns = document.querySelectorAll('input.progress-tracker-update-button');
                for (var b of btns) {
                    if (b.offsetParent !== null) { b.click(); return 'visible'; }
                }
                if (btns.length > 0) { btns[0].click(); return 'hidden'; }
                return 'none';
            """)
            logger.debug("Save button JS result: %s", save_result)
            if save_result == 'none':
                logger.error("Save button not found after filling progress")
                return False
            time.sleep(2)

            logger.info(
                "StoryGraph progress saved: %s pages",
                pages if pages is not None else f"~{int(total_pages * pct / 100)}",
            )
            return True

        except TimeoutException:
            logger.error("Timed out interacting with StoryGraph progress form")
            return False
        except Exception as exc:
            logger.error("Error filling StoryGraph progress form: %s", exc)
            return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"\s*\(.*?\)", "", text)
    return text.strip()
