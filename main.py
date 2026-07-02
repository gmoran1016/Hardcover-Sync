"""Hardcover Sync entry point and resilient synchronization orchestration."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time

from config import Config, ConfigError, load_config
from goodreads import COOKIES_FILE as GOODREADS_COOKIES, GoodreadsSync
from hardcover import HardcoverAPIError, get_book_statuses, get_currently_reading
from storygraph import COOKIES_FILE as STORYGRAPH_COOKIES, StorygraphSync
from sync_result import SyncResult
from sync_state import StateError, load_state, progress_signature, save_state

VERSION = "2.0.5"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger(__name__)


def _enabled(cookie_file: str, email: str, password: str) -> bool:
    return os.path.exists(cookie_file) or bool(email and password)


def _coerce_result(value, target_url: str | None = None) -> SyncResult:
    if isinstance(value, SyncResult):
        return value
    return SyncResult.ok(target_url) if value else SyncResult.failed("operation failed")


def _sync_destination(
    name: str,
    adapter,
    destination_state: dict,
    current_books: dict[str, dict],
    pending_finished: dict[str, dict],
) -> tuple[int, int, int]:
    succeeded = failed = skipped = 0
    synced = destination_state["books"]
    mappings = destination_state["mappings"]

    for book_id, book in current_books.items():
        signature = progress_signature(book)
        if synced.get(book_id) == signature:
            skipped += 1
            continue
        result = _coerce_result(
            adapter.update_progress(book, mappings.get(book_id)),
            mappings.get(book_id),
        )
        if result.success:
            synced[book_id] = signature
            if result.target_url:
                mappings[book_id] = result.target_url
            succeeded += 1
        else:
            failed += 1
            logger.error("%s update failed for '%s': %s", name, book["title"], result.reason)

    for book_id, book in pending_finished.items():
        if synced.get(book_id) == "finished":
            skipped += 1
            continue
        result = _coerce_result(
            adapter.mark_finished(book, mappings.get(book_id)),
            mappings.get(book_id),
        )
        if result.success:
            synced[book_id] = "finished"
            if result.target_url:
                mappings[book_id] = result.target_url
            succeeded += 1
        else:
            failed += 1
            logger.error("%s finish failed for '%s': %s", name, book["title"], result.reason)

    return succeeded, failed, skipped


def run_sync(config: Config) -> None:
    try:
        state = load_state(config.state_file)
    except StateError as exc:
        logger.error("%s — refusing to overwrite it", exc)
        return

    try:
        books = get_currently_reading(config.hardcover_api_key)
        current_books = {book["id"]: book for book in books}
        previous_books = state["source_books"]
        missing_ids = [
            int(book["user_book_id"])
            for key, book in previous_books.items()
            if (
                key not in current_books
                and key not in state["pending_finished"]
                and book.get("user_book_id") is not None
            )
        ]
        statuses = get_book_statuses(config.hardcover_api_key, missing_ids)
    except HardcoverAPIError as exc:
        logger.error("%s — preserving state and skipping this cycle", exc)
        return

    for book_id, status in statuses.items():
        if status.get("status_id") == 3:
            state["pending_finished"][book_id] = status
            logger.info("Detected finished book: '%s'", status["title"])
        else:
            logger.info(
                "'%s' left Currently Reading with status %s; no finish action queued",
                status["title"],
                status.get("status_id"),
            )

    state["source_books"] = current_books
    totals = {"succeeded": 0, "failed": 0, "skipped": 0}

    destinations = [
        (
            "Goodreads",
            "goodreads",
            _enabled(GOODREADS_COOKIES, config.goodreads_email, config.goodreads_password),
            lambda: GoodreadsSync(config.goodreads_email, config.goodreads_password),
        ),
        (
            "StoryGraph",
            "storygraph",
            _enabled(STORYGRAPH_COOKIES, config.storygraph_email, config.storygraph_password),
            lambda: StorygraphSync(config.storygraph_email, config.storygraph_password),
        ),
    ]

    for display_name, state_name, enabled, factory in destinations:
        if not enabled:
            logger.info("%s is not configured — skipping", display_name)
            continue
        try:
            with factory() as adapter:
                if not adapter.login():
                    logger.error("%s login failed; pending work will be retried", display_name)
                    totals["failed"] += len(current_books) + len(state["pending_finished"])
                    continue
                counts = _sync_destination(
                    display_name,
                    adapter,
                    state["destinations"][state_name],
                    current_books,
                    state["pending_finished"],
                )
                for key, value in zip(("succeeded", "failed", "skipped"), counts):
                    totals[key] += value
        except Exception as exc:
            logger.exception("%s session failed: %s", display_name, exc)
            totals["failed"] += len(current_books) + len(state["pending_finished"])

    try:
        save_state(config.state_file, state)
    except OSError:
        return

    logger.info(
        "Sync complete: %d succeeded, %d failed, %d unchanged",
        totals["succeeded"],
        totals["failed"],
        totals["skipped"],
    )


def run_auth_diagnostics(config: Config) -> bool:
    """Test configured destination logins without modifying reading progress."""
    logger.info("Running authentication diagnostics (no progress will be changed)")
    checks = [
        (
            "Goodreads",
            _enabled(GOODREADS_COOKIES, config.goodreads_email, config.goodreads_password),
            lambda: GoodreadsSync(config.goodreads_email, config.goodreads_password),
        ),
        (
            "StoryGraph",
            _enabled(STORYGRAPH_COOKIES, config.storygraph_email, config.storygraph_password),
            lambda: StorygraphSync(config.storygraph_email, config.storygraph_password),
        ),
    ]
    success = True
    for name, enabled, factory in checks:
        if not enabled:
            logger.info("%s is not configured — skipped", name)
            continue
        try:
            with factory() as adapter:
                if adapter.login():
                    logger.info("%s authentication diagnostic: PASS", name)
                else:
                    logger.error("%s authentication diagnostic: FAIL", name)
                    success = False
        except Exception as exc:
            logger.exception("%s authentication diagnostic crashed: %s", name, exc)
            success = False
    logger.info("Authentication diagnostics complete: %s", "PASS" if success else "FAIL")
    return success


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync Hardcover progress")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--once",
        action="store_true",
        help="run one complete sync and exit instead of waiting for the next interval",
    )
    mode.add_argument(
        "--diagnose-auth",
        action="store_true",
        help="test destination cookie/login authentication without changing progress",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    try:
        config = load_config()
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        raise SystemExit(2) from exc

    interval_minutes = config.sync_interval_seconds // 60
    logger.info("Hardcover Sync v%s starting (interval: %d min)", VERSION, interval_minutes)

    if args.diagnose_auth:
        raise SystemExit(0 if run_auth_diagnostics(config) else 1)
    if args.once:
        run_sync(config)
        logger.info("One-shot sync complete")
        return

    running = True

    def _stop(_sig, _frame):
        nonlocal running
        logger.info("Shutdown signal received")
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    while running:
        try:
            run_sync(config)
        except Exception as exc:
            logger.exception("Unexpected error during sync: %s", exc)
        if not running:
            break
        logger.info("Next sync in %d minutes", interval_minutes)
        for _ in range(config.sync_interval_seconds):
            if not running:
                break
            time.sleep(1)

    logger.info("Hardcover Sync stopped")


if __name__ == "__main__":
    main()
