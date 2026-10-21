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
import time
from urllib.parse import quote_plus, urljoin

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
)

from cookie_bundle import load_cookie_bundle
from driver import create_driver, set_user_agent
from matching import canonical_book_url, choose_match, normalise
from sync_result import SyncResult

logger = logging.getLogger(__name__)
GOODREADS_URL = "https://www.goodreads.com"
COOKIES_FILE = os.path.join(os.path.dirname(__file__), "cookies", "goodreads.json")


class GoodreadsSync:
    """Context-manager that owns a single browser session for Goodreads."""

    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.driver = None
        self._last_widget_url = None
        self._last_save_result = None

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
            if self.email and self.password:
                logger.info("Trying Goodreads form login as a fallback")
                return self._login_with_form()
            return False

        logger.warning(
            "No cookies file found at %s. "
            "Run 'python setup_cookies.py' to create it. "
            "Falling back to form login (may hit CAPTCHA).",
            COOKIES_FILE,
        )
        return self._login_with_form()

    def mark_finished(self, book: dict, book_url: str | None = None) -> SyncResult:
        """Mark a book as finished on Goodreads via the 'I'm finished!' button."""
        title = book["title"]
        logger.info("Marking Goodreads as finished: '%s'", title)
        target_url = canonical_book_url("goodreads", book_url) if book_url else None
        if book_url and not target_url:
            return SyncResult.failed(
                "unsafe or invalid Goodreads mapping", retryable=False
            )
        try:
            target_url = target_url or self._search_book(title, book.get("author"))
            if target_url:
                self.driver.get(target_url)
                time.sleep(1)
                if self._is_read_book_page():
                    return SyncResult.ok(target_url)
            else:
                logger.debug(
                    "No direct Goodreads result for '%s'; checking the current-reading widget",
                    title,
                )

            self.driver.get(GOODREADS_URL)
            time.sleep(2)

            if not self._click_update_progress_for(
                title, target_url, book.get("author")
            ):
                logger.warning(
                    "'%s' not in Currently Reading widget; can't mark finished", title
                )
                return SyncResult.failed(
                    "book is not safely identifiable in the Currently Reading widget",
                    target_url=target_url,
                )

            time.sleep(1)
            wait = WebDriverWait(self.driver, 8)
            finished_btn = wait.until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        "//*[self::button or self::a][contains(translate("
                        'normalize-space(.), "ABCDEFGHIJKLMNOPQRSTUVWXYZ", '
                        '"abcdefghijklmnopqrstuvwxyz"), "finished") or '
                        "normalize-space(translate(normalize-space(.), "
                        '"ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"))='
                        '"mark as read"]',
                    )
                )
            )
            finished_btn.click()
            try:
                WebDriverWait(self.driver, 10).until(EC.staleness_of(finished_btn))
            except TimeoutException:
                # Goodreads may keep the updated control mounted. The fresh book
                # page check below is the actual confirmation.
                logger.debug("Finished control remained mounted for '%s'", title)
            confirmed_url = target_url or self._last_widget_url
            if confirmed_url:
                self.driver.get(confirmed_url)
                time.sleep(1)
                if not self._is_read_book_page():
                    return SyncResult.failed(
                        "Goodreads did not confirm the Read shelf",
                        target_url=confirmed_url,
                    )
            logger.info("Goodreads: marked '%s' as finished", title)
            return SyncResult.ok(confirmed_url)

        except TimeoutException:
            logger.error("Timed out finding 'I'm finished!' button for '%s'", title)
            return SyncResult.failed("finished button timed out", target_url=target_url)
        except Exception as exc:
            logger.error("Error marking '%s' as finished on Goodreads: %s", title, exc)
            return SyncResult.failed(str(exc), target_url=target_url)

    def update_progress(self, book: dict, book_url: str | None = None) -> SyncResult:
        title = book["title"]
        pages = book.get("progress_pages")
        pct = book.get("progress_percent")

        logger.info(
            "Updating Goodreads: '%s' → %s pages / %.1f%%",
            title,
            pages if pages is not None else "?",
            pct or 0.0,
        )

        target_url = canonical_book_url("goodreads", book_url) if book_url else None
        if book_url and not target_url:
            return SyncResult.failed(
                "unsafe or invalid Goodreads mapping", retryable=False
            )
        try:
            # The "Update progress" button lives on the home page's Currently Reading widget,
            # not on the individual book page.
            self.driver.get(GOODREADS_URL)
            time.sleep(2)

            if not self._click_update_progress_for(
                title, target_url, book.get("author")
            ):
                # Book isn't in Currently Reading widget — add it first via the book page
                logger.info(
                    "Book not in Currently Reading widget; adding it to the shelf…"
                )
                target_url = target_url or self._search_book(title, book.get("author"))
                if target_url:
                    self.driver.get(target_url)
                    time.sleep(5)
                    shelved = self._ensure_currently_reading()
                    if not shelved:
                        logger.error(
                            "Could not add '%s' to Currently Reading shelf — "
                            "check DEBUG logs for shelf button details",
                            title,
                        )
                        return SyncResult.failed(
                            "could not move book to Currently Reading",
                            target_url=target_url,
                        )
                    # Give Goodreads time to propagate the shelf change to the home widget
                    time.sleep(4)
                    self.driver.get(GOODREADS_URL)
                    time.sleep(3)
                    if not self._click_update_progress_for(
                        title, target_url, book.get("author")
                    ):
                        logger.error(
                            "Still can't find Update progress for '%s' after adding to shelf — "
                            "the Currently Reading widget may take longer to appear; "
                            "try running sync again in a few minutes",
                            title,
                        )
                        return SyncResult.failed(
                            "progress widget did not appear after shelving",
                            target_url=target_url,
                        )
                else:
                    return SyncResult.failed("no unambiguous Goodreads search result")

            # The widget's canonical URL is the identity we selected when no
            # persisted mapping was supplied. Use it for post-save verification
            # instead of treating a disappearing form as proof of success.
            target_url = target_url or self._last_widget_url
            time.sleep(1)
            result = self._fill_home_page_progress_form(pages, pct)
            if result and self._last_save_result is False:
                return SyncResult.failed(
                    "Goodreads reported that progress was not saved",
                    target_url=target_url,
                )
            if result and self._last_save_result is None and target_url:
                self.driver.get(target_url)
                time.sleep(1)
                if not self._verify_book_progress(pages, pct):
                    return SyncResult.failed(
                        "Goodreads did not confirm saved progress",
                        target_url=target_url,
                    )
            return (
                SyncResult.ok(target_url or self._last_widget_url)
                if result
                else SyncResult.failed(
                    "progress form could not be saved",
                    target_url=target_url,
                )
            )

        except Exception as exc:
            logger.error("Error updating '%s' on Goodreads: %s", title, exc)
            return SyncResult.failed(str(exc), target_url=target_url)

    # ------------------------------------------------------------------
    # Login helpers
    # ------------------------------------------------------------------

    def _login_with_cookies(self) -> bool:
        logger.info("Loading Goodreads session from saved cookies…")
        try:
            bundle = load_cookie_bundle(COOKIES_FILE)
        except (OSError, ValueError) as exc:
            logger.error("Could not read cookies file: %s", exc)
            return False

        if bundle.user_agent:
            set_user_agent(
                self.driver,
                bundle.user_agent,
                bundle.user_agent_metadata,
            )
            logger.info("Using browser identity captured with Goodreads cookies")
        else:
            logger.warning(
                "Goodreads cookies use the legacy format without browser identity. "
                "Re-run setup_cookies.py once after upgrading."
            )

        # Must navigate to the domain before adding cookies
        self.driver.get(GOODREADS_URL)
        time.sleep(1)

        for cookie in bundle.cookies:
            cookie.pop("sameSite", None)  # can cause errors in some versions
            try:
                self.driver.add_cookie(cookie)
            except Exception as exc:
                logger.debug("Skipped cookie '%s': %s", cookie.get("name"), exc)

        # Use a fresh navigation rather than refresh. Goodreads' WAF can return
        # an empty 202 response when a newly cookie-seeded session is refreshed.
        self.driver.get(GOODREADS_URL)
        for elapsed in range(0, 61, 5):
            if self._is_logged_in():
                logger.info("Goodreads authenticated via saved cookies")
                return True
            if elapsed:
                logger.info(
                    "Waiting for Goodreads authentication challenge… %d seconds "
                    "(title: %r)",
                    elapsed,
                    self.driver.title,
                )
            time.sleep(5)

        logger.warning(
            "Goodreads cookie authentication was rejected after 60 seconds "
            "(URL: %s, title: %r, page bytes: %d)",
            self.driver.current_url,
            self.driver.title,
            len(self.driver.page_source),
        )
        return False

    def _login_with_form(self) -> bool:
        """Form-based login fallback — may hit Amazon's CAPTCHA in headless mode."""
        logger.info("Attempting Goodreads form login…")
        try:
            self.driver.get(f"{GOODREADS_URL}/user/sign_in")
            wait = WebDriverWait(self.driver, 15)

            # Goodreads shows OAuth buttons first; click "Sign in with email"
            wait.until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        '//button[contains(normalize-space(.), "Sign in with email")]'
                        ' | //a[contains(normalize-space(.), "Sign in with email")]',
                    )
                )
            ).click()
            time.sleep(1)

            wait.until(
                EC.presence_of_element_located(
                    (
                        By.CSS_SELECTOR,
                        'input#user_email, input[name="user[email]"], input[type="email"]',
                    )
                )
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
                    'a[href*="/user/show"], a[href*="/review/list"]',
                )
                return True
            except NoSuchElementException:
                pass
        return False

    # ------------------------------------------------------------------
    # Book search & shelf management
    # ------------------------------------------------------------------

    def _search_book(self, title: str, author: str | None) -> str | None:
        query = (
            f"{title} {author}" if author and author not in ("Unknown", "") else title
        )
        self.driver.get(
            f"{GOODREADS_URL}/search?q={quote_plus(query)}&search_type=books"
        )

        candidates = []
        try:
            wait = WebDriverWait(self.driver, 10)
            links = wait.until(
                EC.presence_of_all_elements_located(
                    (
                        By.CSS_SELECTOR,
                        "a.bookTitle, td.title a, [data-testid='bookTitle']",
                    )
                )
            )

            for link in links[:5]:
                text = link.text
                try:
                    row = link.find_element(By.XPATH, "./ancestor::tr[1]")
                    text = row.text or text
                except NoSuchElementException:
                    pass
                candidates.append((text, link.get_attribute("href")))

        except TimeoutException:
            logger.warning(
                "Goodreads search page timed out for '%s'; trying autocomplete",
                title,
            )

        match = choose_match(title, author, candidates)
        if match:
            safe_match = canonical_book_url("goodreads", match)
            if safe_match:
                logger.debug("Matched Goodreads result for '%s': %s", title, safe_match)
                return safe_match

        return self._search_book_autocomplete(title, author, query)

    def _search_book_autocomplete(
        self, title: str, author: str | None, query: str
    ) -> str | None:
        endpoint = (
            f"{GOODREADS_URL}/book/auto_complete?format=json&q={quote_plus(query)}"
        )
        try:
            self.driver.get(endpoint)
            payload = json.loads(self.driver.find_element(By.TAG_NAME, "body").text)
        except (NoSuchElementException, TimeoutException, TypeError, ValueError) as exc:
            logger.warning(
                "Goodreads autocomplete search failed for '%s': %s", title, exc
            )
            return None
        if not isinstance(payload, list):
            logger.warning(
                "Goodreads autocomplete returned invalid results for '%s'", title
            )
            return None

        candidates = []
        for item in payload[:10]:
            if not isinstance(item, dict):
                continue
            candidate_title = item.get("bookTitleBare") or item.get("title")
            candidate_url = item.get("bookUrl") or item.get("url")
            if not isinstance(candidate_title, str) or not isinstance(
                candidate_url, str
            ):
                continue
            candidate_author = item.get("author")
            if isinstance(candidate_author, dict):
                candidate_author = candidate_author.get("name")
            evidence = candidate_title
            if isinstance(candidate_author, str) and candidate_author.strip():
                evidence += f"\n{candidate_author.strip()}"
            safe_url = canonical_book_url(
                "goodreads", urljoin(GOODREADS_URL, candidate_url)
            )
            if safe_url:
                candidates.append((evidence, safe_url))

        match = choose_match(title, author, candidates)
        if not match:
            logger.warning("No unambiguous Goodreads match for '%s'", title)
            return None
        logger.debug("Matched Goodreads autocomplete result for '%s': %s", title, match)
        return match

    def _ensure_currently_reading(self) -> bool:
        """Click the shelf button and pick 'Currently reading' from the dropdown.

        Returns True if the book is (or was already) on the Currently Reading shelf.
        """
        try:
            wait = WebDriverWait(self.driver, 20)
            # Goodreads uses several button styles for the shelf selector depending
            # on whether the book is already shelved and which UI version is served.
            shelf_btn = wait.until(
                EC.presence_of_element_located(
                    (
                        By.XPATH,
                        "//button[contains(@class,'Button--block') or "
                        "contains(@class,'wantToReadBtn') or "
                        "contains(@class,'shelving-control') or "
                        "contains(normalize-space(.),'Want to Read') or "
                        "contains(normalize-space(.),'Currently reading') or "
                        "contains(normalize-space(.),'Read')]"
                        "[not(contains(normalize-space(.),'Update progress'))]",
                    )
                )
            )
            btn_text = shelf_btn.text.lower()
            logger.debug("Shelf button found, text: %r", shelf_btn.text.strip())

            if "currently reading" in btn_text:
                logger.debug("Book already on Currently Reading shelf")
                return True

            self.driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", shelf_btn
            )
            time.sleep(0.3)
            self.driver.execute_script("arguments[0].click();", shelf_btn)
            time.sleep(0.8)

            cr_option = WebDriverWait(self.driver, 15).until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        '//button[normalize-space(.)="Currently Reading"]'
                        ' | //button[normalize-space(.)="Currently reading"]'
                        ' | //a[normalize-space(.)="Currently Reading"]'
                        ' | //li[normalize-space(.)="Currently Reading"]',
                    )
                )
            )
            cr_option.click()
            time.sleep(1.5)
            logger.info("Added book to Currently Reading shelf")
            return True

        except TimeoutException:
            logger.warning(
                "_ensure_currently_reading: timed out finding shelf button or dropdown option — "
                "Goodreads UI may have changed or the book page did not load correctly"
            )
            return False
        except NoSuchElementException as exc:
            logger.warning("_ensure_currently_reading: element not found — %s", exc)
            return False
        except Exception as exc:
            logger.warning("_ensure_currently_reading: unexpected error — %s", exc)
            return False

    # ------------------------------------------------------------------
    # Home-page progress update (the only place Goodreads shows the form)
    # ------------------------------------------------------------------

    def _is_read_book_page(self) -> bool:
        for selector in (
            "button.Button--block",
            "[data-testid='shelf-status']",
            ".bookPage__shelfButton",
            "button[aria-label]",
        ):
            for element in self.driver.find_elements(By.CSS_SELECTOR, selector):
                if not element.is_displayed():
                    continue
                if normalise(element.text) in {"read", "already read"}:
                    return True
                accessible_status = normalise(element.get_attribute("aria-label") or "")
                if (
                    accessible_status == "shelved as read"
                    or accessible_status.startswith("shelved as read ")
                ):
                    return True
        return False

    def _verify_book_progress(self, pages: int | None, pct: float | None) -> bool:
        expected = str(pages if pages is not None else int(pct or 0))
        for element in self.driver.find_elements(
            By.CSS_SELECTOR,
            "input.updateReadingProgress__headerInput, [data-testid='progress-value']",
        ):
            if (
                element.is_displayed()
                and str(element.get_attribute("value") or element.text).strip()
                == expected
            ):
                return True
        return expected in normalise(getattr(self.driver, "page_source", "")).split()

    def _click_update_progress_for(
        self, title: str, target_url: str | None = None, author: str | None = None
    ) -> bool:
        """Click only the widget whose canonical book URL matches the request."""
        try:
            wait = WebDriverWait(self.driver, 5)
            btns = wait.until(
                EC.presence_of_all_elements_located(
                    (By.XPATH, '//button[normalize-space(.)="Update progress"]')
                )
            )
        except TimeoutException:
            logger.debug("No 'Update progress' buttons found on home page")
            return False

        def js_click(el):
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block:'center'});", el
            )
            time.sleep(0.3)
            self.driver.execute_script("arguments[0].click();", el)

        target_url = canonical_book_url("goodreads", target_url) if target_url else None
        candidates = []
        for btn in btns:
            try:
                container = btn.find_element(
                    By.XPATH, "./ancestor::div[.//a[contains(@href,'/book/show/')]][1]"
                )
                links = container.find_elements(
                    By.CSS_SELECTOR, "a[href*='/book/show/']"
                )
                href = links[0].get_attribute("href") if links else None
                canonical = canonical_book_url("goodreads", href)
                if canonical:
                    candidates.append((container.text, canonical, btn))
            except NoSuchElementException:
                continue

        chosen = None
        if target_url:
            chosen = next((item for item in candidates if item[1] == target_url), None)
        else:
            match = choose_match(
                title, author, [(text, url) for text, url, _btn in candidates]
            )
            chosen = next((item for item in candidates if item[1] == match), None)
        if chosen:
            js_click(chosen[2])
            self._last_widget_url = chosen[1]
            logger.debug("Clicked Update progress for '%s'", title)
            return True
        logger.warning(
            "Could not safely associate an Update progress button with '%s'", title
        )
        return False

    def _fill_home_page_progress_form(
        self, pages: int | None, pct: float | None
    ) -> bool:
        """Fill and submit the inline progress form that appears after clicking Update progress."""
        try:
            self._last_save_result = None
            wait = WebDriverWait(self.driver, 15)

            # Both pages and percent modes use the same input element:
            # class="gr-textInput updateReadingProgress__headerInput"
            # In pages mode the placeholder is "p. NNN"; in percent mode it's
            # just a number. We locate by class, which works for both.
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

                progress_input = wait.until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, "input.updateReadingProgress__headerInput")
                    )
                )
                progress_input.clear()
                progress_input.send_keys(str(pages))

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

                progress_input = wait.until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, "input.updateReadingProgress__headerInput")
                    )
                )
                progress_input.clear()
                progress_input.send_keys(str(int(pct)))

            else:
                logger.error("No pages or percent to submit")
                return False

            # The submit button has a unique class that distinguishes it from
            # the widget "Update progress" buttons. No form element wraps it —
            # the container is div.longTextPopupForm.
            submit = wait.until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, "button.longTextPopupForm__submitButton")
                )
            )
            try:
                self.driver.execute_script(
                    """
                    if (!window.__hardcoverSyncFetchWrapped && window.fetch) {
                      window.__hardcoverSyncFetchWrapped = true;
                      const originalFetch = window.fetch;
                      window.fetch = function() {
                        return originalFetch.apply(this, arguments).then(function(response) {
                          response.clone().json().then(function(payload) {
                            if (payload && typeof payload.success === 'boolean') {
                              window.__hardcoverSyncLastSave = payload.success;
                            }
                          }).catch(function() {});
                          return response;
                        });
                      };
                    }
                    if (!window.__hardcoverSyncXHRWrapped && window.XMLHttpRequest) {
                      window.__hardcoverSyncXHRWrapped = true;
                      const OriginalXHR = window.XMLHttpRequest;
                      window.XMLHttpRequest = function() {
                        const xhr = new OriginalXHR();
                        xhr.addEventListener('load', function() {
                          try {
                            const payload = JSON.parse(xhr.responseText);
                            if (payload && typeof payload.success === 'boolean') {
                              window.__hardcoverSyncLastSave = payload.success;
                            }
                          } catch (_) {}
                        });
                        return xhr;
                      };
                      window.XMLHttpRequest.prototype = OriginalXHR.prototype;
                    }
                    window.__hardcoverSyncLastSave = null;
                    """
                )
            except Exception:
                pass
            submit.click()
            wait.until(
                EC.invisibility_of_element_located(
                    (
                        By.CSS_SELECTOR,
                        "button.longTextPopupForm__submitButton",
                    )
                )
            )
            try:
                self._last_save_result = self.driver.execute_script(
                    "return window.__hardcoverSyncLastSave;"
                )
            except Exception:
                self._last_save_result = None

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
