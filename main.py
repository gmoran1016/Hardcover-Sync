"""Hardcover Sync — main entry point.

Polls Hardcover every SYNC_INTERVAL_MINUTES minutes and pushes reading
progress to Goodreads (required) and StoryGraph (optional).
"""

import json
import logging
import os
import signal
import time

VERSION = "1.2.6"

from dotenv import load_dotenv

from hardcover import get_currently_reading, get_finished_books
from goodreads import GoodreadsSync
from storygraph import StorygraphSync

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
load_dotenv()

HARDCOVER_API_KEY = os.getenv("HARDCOVER_API_KEY", "")
GOODREADS_EMAIL = os.getenv("GOODREADS_EMAIL", "")
GOODREADS_PASSWORD = os.getenv("GOODREADS_PASSWORD", "")
STORYGRAPH_EMAIL = os.getenv("STORYGRAPH_EMAIL", "")
STORYGRAPH_PASSWORD = os.getenv("STORYGRAPH_PASSWORD", "")
SYNC_INTERVAL = int(os.getenv("SYNC_INTERVAL_MINUTES", "30")) * 60

# Stored in a dedicated named volume so appuser can always write to it
STATE_FILE = os.path.join(os.path.dirname(__file__), "state", "sync_state.json")


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"reading_titles": [], "books": []}


def _save_state(reading_titles: list[str], books: list[dict]) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump({"reading_titles": reading_titles, "books": books}, f)
    except OSError as exc:
        logger.warning("Could not save sync state: %s", exc)


def _books_changed(prev_books: list[dict], curr_books: list[dict]) -> bool:
    """Return True if the book list or any reading progress has changed."""
    if len(prev_books) != len(curr_books):
        return True
    prev_map = {b["title"]: b for b in prev_books}
    curr_map = {b["title"]: b for b in curr_books}
    if set(prev_map) != set(curr_map):
        return True
    for title, curr in curr_map.items():
        prev = prev_map[title]
        if curr.get("progress_pages") != prev.get("progress_pages"):
            return True
        if curr.get("progress_percent") != prev.get("progress_percent"):
            return True
    return False


# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------

def run_sync() -> None:
    if not HARDCOVER_API_KEY:
        logger.error("HARDCOVER_API_KEY is not set — nothing to do")
        return

    # 1. Fetch current reading list and compare with last sync to detect finished books
    state = _load_state()
    prev_titles = set(state.get("reading_titles", []))
    prev_books = state.get("books", [])

    books = get_currently_reading(HARDCOVER_API_KEY)
    current_titles = {b["title"] for b in books}

    # Titles that were reading last sync but are gone now — check if they're finished
    missing = list(prev_titles - current_titles)
    finished_books = get_finished_books(HARDCOVER_API_KEY, missing) if missing else []
    if finished_books:
        logger.info(
            "Detected %d finished book(s): %s",
            len(finished_books),
            ", ".join(f"'{b['title']}'" for b in finished_books),
        )

    if not books and not finished_books:
        logger.info("No currently-reading or newly-finished books found on Hardcover")
        _save_state([], [])
        return

    if books:
        logger.info("Found %d currently-reading book(s) on Hardcover", len(books))

    # Skip pushing updates if nothing changed on Hardcover
    if not finished_books and not _books_changed(prev_books, books):
        logger.info("No changes detected on Hardcover — skipping sync")
        return

    # 2. Push to Goodreads (required)
    if GOODREADS_EMAIL and GOODREADS_PASSWORD:
        with GoodreadsSync(GOODREADS_EMAIL, GOODREADS_PASSWORD) as gr:
            if gr.login():
                for book in books:
                    gr.update_progress(book)
                for book in finished_books:
                    gr.mark_finished(book)
            else:
                logger.error("Goodreads login failed — skipping Goodreads sync")
    else:
        logger.warning(
            "GOODREADS_EMAIL / GOODREADS_PASSWORD not set — skipping Goodreads sync"
        )

    # 3. Push to StoryGraph (optional)
    if STORYGRAPH_EMAIL and STORYGRAPH_PASSWORD:
        with StorygraphSync(STORYGRAPH_EMAIL, STORYGRAPH_PASSWORD) as sg:
            if sg.login():
                for book in books:
                    sg.update_progress(book)
                for book in finished_books:
                    sg.mark_finished(book)
            else:
                logger.error("StoryGraph login failed — skipping StoryGraph sync")
    else:
        logger.info("StoryGraph credentials not set — skipping StoryGraph sync")

    # 4. Save state for next sync
    _save_state(list(current_titles), books)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    logger.info("Hardcover Sync v%s starting (interval: %d min)", VERSION, SYNC_INTERVAL // 60)

    running = True

    def _stop(sig, _frame):
        nonlocal running
        logger.info("Shutdown signal received")
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    while running:
        try:
            run_sync()
        except Exception as exc:
            logger.exception("Unexpected error during sync: %s", exc)

        if not running:
            break

        logger.info("Next sync in %d minutes", SYNC_INTERVAL // 60)
        # Sleep in 1-second ticks so SIGTERM is handled promptly
        for _ in range(SYNC_INTERVAL):
            if not running:
                break
            time.sleep(1)

    logger.info("Hardcover Sync stopped")


if __name__ == "__main__":
    main()
