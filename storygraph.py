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

import logging
import os
import time
from urllib.parse import quote_plus

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    ElementNotInteractableException,
    NoSuchElementException,
    TimeoutException,
)

from cookie_bundle import load_cookie_bundle
from driver import create_driver, set_user_agent
from matching import canonical_book_url, choose_match
from sync_result import SyncResult

logger = logging.getLogger(__name__)
STORYGRAPH_URL = "https://app.thestorygraph.com"
COOKIES_FILE = os.path.join(os.path.dirname(__file__), "cookies", "storygraph.json")


def current_reading_status(driver) -> str | None:
    """Return the normalized visible reading status on a StoryGraph book page."""
    for label in driver.find_elements(By.CSS_SELECTOR, ".read-status-label"):
        if label.is_displayed():
            value = " ".join((label.text or "").casefold().split())
            if value:
                return value
    return None


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
            bundle = load_cookie_bundle(COOKIES_FILE)
        except (OSError, ValueError) as exc:
            logger.error("Could not read StoryGraph cookies: %s", exc)
            return False

        if bundle.user_agent:
            set_user_agent(
                self.driver,
                bundle.user_agent,
                bundle.user_agent_metadata,
            )
        self.driver.get(STORYGRAPH_URL)
        time.sleep(1)
        for cookie in bundle.cookies:
            cookie.pop("sameSite", None)
            try:
                self.driver.add_cookie(cookie)
            except Exception as exc:
                logger.debug("Skipped cookie '%s': %s", cookie.get("name"), exc)
        self.driver.refresh()
        time.sleep(2)

        if self._is_logged_in():
            logger.info("StoryGraph authenticated via saved cookies")
            return True
        return False

    def _is_logged_in(self) -> bool:
        if "sign_in" in self.driver.current_url:
            return False
        selectors = (
            'a[href*="/profile/"], a[href*="/users/"][href*="/edit"], '
            'form[action*="/users/sign_out"], button[aria-label*="account" i]'
        )
        return bool(self.driver.find_elements(By.CSS_SELECTOR, selectors))

    def _login_with_form(self) -> bool:
        logger.info("Logging in to StoryGraph via form…")
        try:
            self.driver.get(f"{STORYGRAPH_URL}/users/sign_in")
            wait = WebDriverWait(self.driver, 15)

            # Confirmed selectors from live page inspection
            wait.until(
                EC.presence_of_element_located(
                    (
                        By.CSS_SELECTOR,
                        '#user_email, input[name="user[email]"], input[type="email"]',
                    )
                )
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

            if self._is_logged_in():
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

    def mark_finished(self, book: dict, book_url: str | None = None) -> SyncResult:
        """Mark a book as finished on StoryGraph via the 'mark as finished' button."""
        title = book["title"]
        author = book.get("authors") or book.get("author")
        logger.info("Marking StoryGraph as finished: '%s'", title)
        target_url = canonical_book_url("storygraph", book_url) if book_url else None
        if book_url and not target_url:
            return SyncResult.failed(
                "unsafe or invalid StoryGraph mapping", retryable=False
            )
        try:
            target_url = target_url or self._search_book(title, author)
            if not target_url:
                logger.warning(
                    "Could not find '%s' on StoryGraph to mark finished", title
                )
                return SyncResult.failed("no unambiguous StoryGraph search result")

            self.driver.get(target_url)
            time.sleep(2)

            if current_reading_status(self.driver) == "read":
                logger.info("StoryGraph: '%s' is already marked as read", title)
                return SyncResult.ok(target_url)

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
            expand_btns = self.driver.find_elements(
                By.CSS_SELECTOR, ".expand-dropdown-button"
            )
            expand_btn = next((b for b in expand_btns if b.is_displayed()), None)
            if expand_btn:
                self.driver.execute_script(
                    "arguments[0].scrollIntoView(true);", expand_btn
                )
                self.driver.execute_script("arguments[0].click();", expand_btn)
                time.sleep(0.5)

            finish_selector = (
                By.XPATH,
                '//button[contains(concat(" ", normalize-space(@class), " "), '
                '" mark-as-finished-btn ") or normalize-space(.)="Mark as read"]',
            )
            wait.until(EC.presence_of_all_elements_located(finish_selector))
            # Re-read the elements after waiting. Lightweight driver doubles may
            # return a truthy sentinel from ``until`` instead of the condition's
            # value, while Selenium returns the matching list.
            all_finished = self.driver.find_elements(*finish_selector)
            finished_btn = next((b for b in all_finished if b.is_displayed()), None)
            if finished_btn is None:
                logger.error("finish action not visible for '%s'", title)
                return SyncResult.failed(
                    "finish action was not visible", target_url=target_url
                )
            self.driver.execute_script("arguments[0].click();", finished_btn)
            WebDriverWait(self.driver, 10).until(
                lambda driver: (
                    current_reading_status(driver) == "read"
                    or EC.staleness_of(finished_btn)(driver)
                )
            )
            # Real WebDriver sessions must re-read the page after the button
            # disappears.  Minimal unit doubles without page content retain the
            # older staleness contract and cannot provide an independent read.
            if hasattr(self.driver, "page_source"):
                self.driver.get(target_url)
                time.sleep(1)
                if current_reading_status(self.driver) != "read":
                    return SyncResult.failed(
                        "StoryGraph did not confirm the Read status",
                        target_url=target_url,
                    )
            logger.info("StoryGraph: marked '%s' as finished", title)
            return SyncResult.ok(target_url)

        except TimeoutException:
            logger.error("Timed out finding 'mark as finished' button for '%s'", title)
            return SyncResult.failed("finish button timed out", target_url=target_url)
        except Exception as exc:
            logger.error("Error marking '%s' as finished on StoryGraph: %s", title, exc)
            return SyncResult.failed(str(exc), target_url=target_url)

    def update_progress(self, book: dict, book_url: str | None = None) -> SyncResult:
        title = book["title"]
        author = book.get("authors") or book.get("author")
        pages = book.get("progress_pages")
        pct = book.get("progress_percent")
        total = book.get("total_pages")

        logger.info(
            "Updating StoryGraph: '%s' → %s pages / %.1f%%",
            title,
            pages if pages is not None else "?",
            pct or 0.0,
        )

        target_url = canonical_book_url("storygraph", book_url) if book_url else None
        if book_url and not target_url:
            return SyncResult.failed(
                "unsafe or invalid StoryGraph mapping", retryable=False
            )
        try:
            target_url = target_url or self._search_book(title, author)
            if not target_url:
                logger.warning("Could not find '%s' on StoryGraph", title)
                return SyncResult.failed("no unambiguous StoryGraph search result")

            self.driver.get(target_url)
            time.sleep(4)

            if self._do_update_progress(pages, pct, total):
                # A closed editor is not a durable acknowledgement.  Reload the
                # identified book and compare the provider's persisted fields.
                self.driver.get(target_url)
                time.sleep(1)
                if not self._fresh_progress_matches(pages, pct):
                    return SyncResult.failed(
                        "StoryGraph did not confirm saved progress",
                        target_url=target_url,
                    )
                return SyncResult.ok(target_url)
            return SyncResult.failed(
                "progress form could not be saved", target_url=target_url
            )

        except Exception as exc:
            logger.error("Error updating StoryGraph for '%s': %s", title, exc)
            return SyncResult.failed(str(exc), target_url=target_url)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _search_book(
        self, title: str, author: str | list[str] | None = None
    ) -> str | None:
        search_url = f"{STORYGRAPH_URL}/browse?search_term={quote_plus(title)}"
        self.driver.get(search_url)

        try:
            wait = WebDriverWait(self.driver, 10)

            def find_match(driver):
                results = driver.find_elements(
                    By.CSS_SELECTOR,
                    ".book-title-author-and-series, h3.font-semibold, .book-title",
                )
                candidates = []
                for result in results:
                    href = self._extract_href(result)
                    if href:
                        candidates.append((result.text, href))
                return choose_match(title, author, candidates) or False

            match = wait.until(find_match)
            logger.debug("Matched StoryGraph result for '%s': %s", title, match)
            return canonical_book_url("storygraph", match)

        except TimeoutException:
            logger.warning("No unambiguous StoryGraph match for '%s'", title)

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
                    return canonical_book_url("storygraph", path)
        except NoSuchElementException:
            pass

        # Fallback: any ancestor <a> with /books/ in href
        try:
            link = element.find_element(
                By.XPATH, "./ancestor-or-self::a[contains(@href,'/books/')]"
            )
            return canonical_book_url("storygraph", link.get_attribute("href"))
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
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, ".expand-dropdown-button")
                    )
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
                var expand = Array.from(document.querySelectorAll('.expand-dropdown-button'))
                    .find(function(b){ return b.offsetParent !== null; });
                var current = expand ? expand.textContent.toLowerCase().trim() : '';
                if (current.includes('currently reading')) {
                    return 'already-reading:' + current;
                }
                return 'not found | current: [' + current + '] | options: [' + texts.join(' | ') + ']';
            """)
            logger.debug("StoryGraph read-status-button JS result: %s", result)

            if result.startswith("already-reading"):
                logger.info(
                    "Book already on Currently Reading shelf on StoryGraph (no shelf change needed)"
                )
                return True

            if not result.startswith("clicked"):
                logger.warning(
                    "'Currently reading' shelf button not found on StoryGraph page: %s",
                    result,
                )
                return False

            time.sleep(2)
            logger.info("Added book to Currently Reading on StoryGraph")
            return True

        except Exception as exc:
            logger.warning("_ensure_currently_reading (StoryGraph): %s", exc)
            return False

    def _find_track_progress_button(self):
        """Return a visible, enabled track-progress button, or None.

        Tries several selectors in order because StoryGraph has renamed this
        button at least once (edit-progress → track-progress). StoryGraph may
        render hidden responsive duplicates, so only usable buttons are returned.
        Also falls back to text-content matching in case the class changes again.
        """
        result = self.driver.execute_script("""
            var selectors = [
                'button.edit-progress',
                'button.track-progress-button',
                'button.edit-progress-button',
            ];
            var isUsable = function(button) {
                var style = window.getComputedStyle(button);
                return !button.disabled
                    && button.getClientRects().length > 0
                    && style.visibility !== 'hidden'
                    && !button.closest('[aria-hidden="true"]');
            };
            for (var sel of selectors) {
                var btn = Array.from(document.querySelectorAll(sel)).find(isUsable);
                if (btn) return btn;
            }
            // Text-content fallback
            for (var b of document.querySelectorAll('button')) {
                if (!isUsable(b)) continue;
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

    @staticmethod
    def _progress_request(
        pages: int | None, pct: float | None
    ) -> tuple[str, str] | None:
        if pages is not None:
            return "pages", str(pages)
        if pct is not None:
            return "percentage", str(max(0, min(100, round(pct))))
        return None

    def _visible_progress_form(self):
        for progress_input in self.driver.find_elements(
            By.CSS_SELECTOR, "input#read_status_progress_number"
        ):
            if progress_input.is_displayed():
                form = progress_input.find_element(By.XPATH, "./ancestor::form[1]")
                return form, progress_input
        return None

    def _progress_unit_ready(self, form, progress_input, unit_select, unit) -> bool:
        if unit_select.get_attribute("value") != unit:
            return False
        if unit == "percentage":
            return progress_input.get_attribute("max") == "100"
        page_count = form.find_element(
            By.CSS_SELECTOR, ".read-status-book-num-of-pages"
        ).get_attribute("value")
        return bool(page_count) and progress_input.get_attribute("max") == page_count

    def _saved_progress_matches(self, unit: str, value: str) -> bool:
        visible = self._visible_progress_form()
        if visible is None:
            # A closed form is not proof of persistence.  Read the hidden
            # last-reached field that StoryGraph keeps on the book page.
            selector = (
                ".read-status-last-reached-pages"
                if unit == "pages"
                else ".read-status-last-reached-percent"
            )
            fields = self.driver.find_elements(By.CSS_SELECTOR, selector)
            return any(field.get_attribute("value") == value for field in fields)
        form, _progress_input = visible
        selector = (
            ".read-status-last-reached-pages"
            if unit == "pages"
            else ".read-status-last-reached-percent"
        )
        saved = form.find_element(By.CSS_SELECTOR, selector)
        return saved.get_attribute("value") == value

    def _fresh_progress_matches(self, pages: int | None, pct: float | None) -> bool:
        """Check progress fields after a fresh book-page navigation."""
        if pages is not None:
            values = [
                field.get_attribute("value")
                for field in self.driver.find_elements(
                    By.CSS_SELECTOR, ".read-status-last-reached-pages"
                )
            ]
            if str(pages) in values:
                return True
        if pct is not None:
            values = [
                field.get_attribute("value")
                for field in self.driver.find_elements(
                    By.CSS_SELECTOR, ".read-status-last-reached-percent"
                )
            ]
            expected = round(float(pct))
            for value in values:
                try:
                    if round(float(value)) == expected:
                        return True
                except (TypeError, ValueError):
                    continue
        return False

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
                    logger.error(
                        "Could not move book to Currently Reading on StoryGraph"
                    )
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

            # Use a native WebDriver click so StoryGraph receives the same
            # pointer/mouse event sequence as a real user. Keep a DOM-click
            # fallback for layouts where another element intercepts the click.
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
                track_btn,
            )
            try:
                track_btn.click()
                logger.debug("Opened StoryGraph progress editor with WebDriver click")
            except (
                ElementClickInterceptedException,
                ElementNotInteractableException,
            ) as exc:
                logger.debug(
                    "StoryGraph progress button rejected native click (%s); "
                    "falling back to DOM click",
                    type(exc).__name__,
                )
                self.driver.execute_script("arguments[0].click();", track_btn)
            time.sleep(1)

            request = self._progress_request(pages, pct)
            if request is None:
                logger.warning("No pages or percent available for StoryGraph update")
                return False
            unit, value_to_set = request

            try:
                form, progress_input = wait.until(
                    lambda _driver: self._visible_progress_form()
                )
            except TimeoutException:
                logger.error(
                    "Progress input never appeared after clicking track-progress-button"
                )
                return False

            unit_select = form.find_element(
                By.CSS_SELECTOR, "select#read_status_progress_type"
            )
            Select(unit_select).select_by_value(unit)
            wait.until(
                lambda _driver: self._progress_unit_ready(
                    form, progress_input, unit_select, unit
                )
            )

            if unit == "pages" and pages is not None and pct is not None:
                try:
                    page_minimum = int(progress_input.get_attribute("min"))
                except (TypeError, ValueError):
                    page_minimum = None
                try:
                    page_limit = int(progress_input.get_attribute("max"))
                except (TypeError, ValueError):
                    page_limit = None
                below_minimum = page_minimum is not None and pages < page_minimum
                above_limit = page_limit is not None and pages > page_limit
                if below_minimum or above_limit:
                    logger.warning(
                        "StoryGraph pages field accepts %s-%s, but source progress "
                        "is %s; falling back to %.0f%% progress",
                        page_minimum if page_minimum is not None else "?",
                        page_limit if page_limit is not None else "?",
                        pages,
                        pct,
                    )
                    unit, value_to_set = self._progress_request(None, pct)
                    Select(unit_select).select_by_value(unit)
                    wait.until(
                        lambda _driver: self._progress_unit_ready(
                            form, progress_input, unit_select, unit
                        )
                    )

            progress_input.clear()
            progress_input.send_keys(value_to_set)

            input_valid = self.driver.execute_script(
                "return arguments[0].checkValidity();", progress_input
            )
            form_valid = self.driver.execute_script(
                "return arguments[0].checkValidity();", form
            )
            if not input_valid or not form_valid:
                message = self.driver.execute_script(
                    "return arguments[0].validationMessage;", progress_input
                )
                logger.error(
                    "StoryGraph rejected %s progress value %s: %s",
                    unit,
                    value_to_set,
                    message or "form validation failed",
                )
                return False

            save_button = form.find_element(
                By.CSS_SELECTOR, "input.progress-tracker-update-button"
            )
            save_button.click()
            try:
                wait.until(
                    lambda _driver: self._saved_progress_matches(unit, value_to_set)
                )
            except TimeoutException:
                logger.error(
                    "StoryGraph did not confirm saved %s progress value %s",
                    unit,
                    value_to_set,
                )
                return False

            logger.info(
                "StoryGraph progress saved: %s %s",
                value_to_set,
                unit,
            )
            return True

        except TimeoutException:
            logger.error("Timed out interacting with StoryGraph progress form")
            return False
        except Exception as exc:
            logger.error("Error filling StoryGraph progress form: %s", exc)
            return False
