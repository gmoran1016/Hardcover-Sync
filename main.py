"""Hardcover Sync — main entry point.

Polls Hardcover every SYNC_INTERVAL_MINUTES minutes and pushes reading
progress to Goodreads (required) and StoryGraph (optional).
"""

import logging
import os
import signal
import time

from dotenv import load_dotenv

from hardcover import get_currently_reading
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


# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------

def run_sync() -> None:
    if not HARDCOVER_API_KEY:
        logger.error("HARDCOVER_API_KEY is not set — nothing to do")
        return

    # 1. Fetch from Hardcover
    books = get_currently_reading(HARDCOVER_API_KEY)
    if not books:
        logger.info("No currently-reading books found on Hardcover")
        return

    logger.info("Found %d book(s) on Hardcover", len(books))

    # 2. Push to Goodreads (required)
    if GOODREADS_EMAIL and GOODREADS_PASSWORD:
        with GoodreadsSync(GOODREADS_EMAIL, GOODREADS_PASSWORD) as gr:
            if gr.login():
                for book in books:
                    gr.update_progress(book)
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
            else:
                logger.error("StoryGraph login failed — skipping StoryGraph sync")
    else:
        logger.info("StoryGraph credentials not set — skipping StoryGraph sync")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    logger.info("Hardcover Sync starting (interval: %d min)", SYNC_INTERVAL // 60)

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
