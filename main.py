"""Hardcover Sync entry point and resilient synchronization orchestration."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time

from config import Config, ConfigError, load_config
from goodreads import COOKIES_FILE as GOODREADS_COOKIES, GoodreadsSync
from hardcover import (
    HardcoverAPIError,
    HardcoverDeferredError,
    get_book_statuses,
    get_currently_reading,
)
from storygraph import COOKIES_FILE as STORYGRAPH_COOKIES, StorygraphSync
from matching import canonical_book_url
from sync_result import SyncResult, SyncRunResult
from sync_state import (
    StateError,
    finished_signature,
    load_state,
    probe_state_storage,
    progress_signature,
    save_state,
    state_lock,
    recover_state,
)

VERSION = "2.1.4"

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger(__name__)
STOP_EVENT = threading.Event()


def _enabled(cookie_file: str, email: str, password: str) -> bool:
    return os.path.exists(cookie_file) or bool(email and password)


def _coerce_result(value, target_url: str | None = None) -> SyncResult:
    if isinstance(value, SyncResult):
        return value
    return SyncResult.ok(target_url) if value else SyncResult.failed("operation failed")


def _run_operation(name: str, action, book: dict, target_url: str | None) -> SyncResult:
    try:
        return _coerce_result(action(book, target_url), target_url)
    except Exception as exc:
        logger.exception(
            "%s operation crashed for '%s': %s", name, book.get("title", "?"), exc
        )
        return SyncResult.failed(
            "unexpected destination operation failure", target_url=target_url
        )


def _record_failure(
    destination_state: dict,
    book_id: str,
    reason: str,
    retryable: bool,
    signature: str,
    target_url: str | None = None,
) -> None:
    failures = destination_state.setdefault("failures", {})
    failures[book_id] = {
        "reason": reason,
        "retryable": bool(retryable),
        "updated_at": time.time(),
        "signature": signature,
        "target_url": target_url,
    }


def _blocked(destination_state: dict, book_id: str, signature: str) -> bool:
    failure = destination_state.setdefault("failures", {}).get(book_id)
    return bool(
        failure
        and failure.get("retryable") is False
        and failure.get("signature") == signature
    )


def _has_source_progress(book: dict) -> bool:
    return (
        book.get("progress_pages") is not None
        or book.get("progress_percent") is not None
    )


def _sync_destination(
    name: str,
    adapter,
    destination_state: dict,
    current_books: dict[str, dict],
    pending_finished: dict[str, dict],
    checkpoint=None,
    cancel_event: threading.Event | None = None,
) -> tuple[int, int, int]:
    succeeded = failed = skipped = 0
    synced = destination_state.setdefault("books", {})
    mappings = destination_state.setdefault("mappings", {})
    destination_state.setdefault("failures", {})

    for book_id, book in current_books.items():
        if cancel_event and cancel_event.is_set():
            break
        if not _has_source_progress(book):
            skipped += 1
            continue
        signature = progress_signature(book)
        if synced.get(book_id) == signature or _blocked(
            destination_state, book_id, signature
        ):
            skipped += 1
            continue
        result = _run_operation(
            name, adapter.update_progress, book, mappings.get(book_id)
        )
        if result.success:
            synced[book_id] = signature
            if result.target_url:
                mappings[book_id] = result.target_url
            destination_state["failures"].pop(book_id, None)
            succeeded += 1
        else:
            failed += 1
            _record_failure(
                destination_state,
                book_id,
                result.reason,
                result.retryable,
                signature,
                result.target_url,
            )
            logger.error(
                "%s update failed for '%s': %s",
                name,
                book.get("title", book_id),
                result.reason,
            )
        if checkpoint:
            checkpoint()

    for book_id, book in pending_finished.items():
        if cancel_event and cancel_event.is_set():
            break
        signature = finished_signature(book)
        if synced.get(book_id) == signature or _blocked(
            destination_state, book_id, signature
        ):
            skipped += 1
            continue
        result = _run_operation(
            name, adapter.mark_finished, book, mappings.get(book_id)
        )
        if result.success:
            synced[book_id] = signature
            if result.target_url:
                mappings[book_id] = result.target_url
            destination_state["failures"].pop(book_id, None)
            succeeded += 1
        else:
            failed += 1
            _record_failure(
                destination_state,
                book_id,
                result.reason,
                result.retryable,
                signature,
                result.target_url,
            )
            logger.error(
                "%s finish failed for '%s': %s",
                name,
                book.get("title", book_id),
                result.reason,
            )
        if checkpoint:
            checkpoint()

    return succeeded, failed, skipped


def _has_destination_work(
    destination_state: dict,
    current_books: dict[str, dict],
    pending_finished: dict[str, dict],
) -> bool:
    synced = destination_state.get("books", {})
    for book_id, book in current_books.items():
        if not _has_source_progress(book):
            continue
        signature = progress_signature(book)
        if synced.get(book_id) != signature and not _blocked(
            destination_state, book_id, signature
        ):
            return True
    for book_id, book in pending_finished.items():
        signature = finished_signature(book)
        if synced.get(book_id) != signature and not _blocked(
            destination_state, book_id, signature
        ):
            return True
    return False


def _has_blocked_work(
    destination_state: dict,
    current_books: dict[str, dict],
    pending_finished: dict[str, dict],
) -> bool:
    failures = destination_state.get("failures", {})
    for book_id, book in current_books.items():
        if not _has_source_progress(book):
            continue
        failure = failures.get(book_id)
        if (
            failure
            and not failure.get("retryable", True)
            and failure.get("signature") == progress_signature(book)
        ):
            return True
    for book_id, book in pending_finished.items():
        failure = failures.get(book_id)
        if (
            failure
            and not failure.get("retryable", True)
            and failure.get("signature") == finished_signature(book)
        ):
            return True
    return False


def _pending_destination_count(
    destination_state: dict,
    current_books: dict[str, dict],
    pending_finished: dict[str, dict],
) -> int:
    synced = destination_state.get("books", {})
    pending = 0
    for book_id, book in current_books.items():
        if not _has_source_progress(book):
            continue
        pending += synced.get(book_id) != progress_signature(book)
    for book_id, book in pending_finished.items():
        pending += synced.get(book_id) != finished_signature(book)
    return pending


def _configured_destinations(config: Config):
    return [
        (
            "Goodreads",
            "goodreads",
            config.goodreads_enabled
            and _enabled(
                GOODREADS_COOKIES, config.goodreads_email, config.goodreads_password
            ),
            lambda: GoodreadsSync(config.goodreads_email, config.goodreads_password),
        ),
        (
            "StoryGraph",
            "storygraph",
            config.storygraph_enabled
            and _enabled(
                STORYGRAPH_COOKIES, config.storygraph_email, config.storygraph_password
            ),
            lambda: StorygraphSync(config.storygraph_email, config.storygraph_password),
        ),
    ]


def _save_checkpoint(path: str, state: dict) -> None:
    save_state(path, state)


def run_sync(
    config: Config,
    *,
    dry_run: bool = False,
    cancel_event: threading.Event | None = None,
) -> SyncRunResult:
    """Run one source reconciliation and destination cycle."""
    try:
        with state_lock(config.state_file):
            try:
                probe_state_storage(config.state_file)
                state = load_state(config.state_file)
            except StateError as exc:
                logger.error("%s — refusing to overwrite it", exc)
                return SyncRunResult(False, str(exc))

            retry_at = state.get("source_retry_at")
            if retry_at and retry_at > time.time():
                logger.warning(
                    "Hardcover retry cooldown active for %.0f more seconds",
                    retry_at - time.time(),
                )
                return SyncRunResult(False, "source retry cooldown active")

            try:
                books = get_currently_reading(config.hardcover_api_key)
                try:
                    current_books = {str(book["id"]): book for book in books}
                except (KeyError, TypeError) as exc:
                    raise HardcoverAPIError(
                        "Hardcover returned a book without a stable id"
                    ) from exc
                previous_books = dict(state["source_books"])
                missing_ids = []
                for key, book in previous_books.items():
                    if (
                        key not in current_books
                        and book.get("user_book_id") is not None
                    ):
                        try:
                            missing_ids.append(int(book["user_book_id"]))
                        except (TypeError, ValueError) as exc:
                            raise HardcoverAPIError(
                                f"Stored Hardcover record '{key}' has an invalid user_book_id"
                            ) from exc
                statuses = get_book_statuses(config.hardcover_api_key, missing_ids)
            except HardcoverDeferredError as exc:
                state["source_retry_at"] = time.time() + exc.retry_after_seconds
                state.setdefault("health", {})["source_error"] = str(exc)
                if not dry_run:
                    try:
                        _save_checkpoint(config.state_file, state)
                    except StateError as save_exc:
                        logger.error("Could not save source cooldown: %s", save_exc)
                return SyncRunResult(False, str(exc))
            except HardcoverAPIError as exc:
                logger.error("%s — preserving state and skipping this cycle", exc)
                return SyncRunResult(False, str(exc))

            state["source_retry_at"] = None
            state.setdefault("health", {})["last_attempt"] = time.time()
            state.setdefault("health", {}).pop("source_error", None)
            known_statuses = {1, 2, 3, 4}
            new_source = {}
            # A currently-reading record is a new read instance if its identity
            # changed.  This cancels an old completion marker before mutation.
            for book_id, book in current_books.items():
                state["pending_finished"].pop(book_id, None)
                new_source[book_id] = book

            for book_id, old_book in previous_books.items():
                if book_id in current_books:
                    continue
                status = statuses.get(book_id)
                if status is None:
                    # Missing status is unresolved and must be retried later.
                    new_source[book_id] = old_book
                    continue
                status_id = status.get("status_id")
                if status_id == 3:
                    state["pending_finished"][book_id] = status
                    new_source[book_id] = status
                    logger.info(
                        "Detected finished book: '%s'", status.get("title", book_id)
                    )
                elif status_id in known_statuses:
                    state["pending_finished"].pop(book_id, None)
                    logger.info(
                        "'%s' left Currently Reading with status %s; no finish action queued",
                        status.get("title", book_id),
                        status_id,
                    )
                else:
                    new_source[book_id] = old_book
                    logger.warning(
                        "Unknown Hardcover status for '%s'; retaining it for retry",
                        book_id,
                    )

            # Pending records may exist after a prior process checkpoint even if
            # their source record was already retired.  Reconcile those status
            # responses as well, especially a completion that was retracted.
            for book_id, status in statuses.items():
                if book_id in previous_books or book_id in current_books:
                    continue
                status_id = status.get("status_id")
                if status_id == 3:
                    state["pending_finished"][book_id] = status
                    new_source[book_id] = status
                elif status_id in known_statuses:
                    state["pending_finished"].pop(book_id, None)
                else:
                    state["pending_finished"].setdefault(book_id, status)
                    new_source[book_id] = status

            # A pending completion with no returned status remains unresolved.
            for book_id, pending in list(state["pending_finished"].items()):
                if book_id not in current_books and book_id not in statuses:
                    new_source.setdefault(book_id, pending)
            state["source_books"] = new_source
            pending_finished = state["pending_finished"]
            current_books = {str(key): value for key, value in current_books.items()}

            if dry_run:
                logger.info(
                    "Dry run: source reconciliation complete; destination mutations skipped"
                )
                return SyncRunResult(True, "dry run")
            _save_checkpoint(config.state_file, state)

            destinations = _configured_destinations(config)
            configured_count = sum(
                1 for _display, _name, enabled, _factory in destinations if enabled
            )
            enabled_count = 0
            totals = {"succeeded": 0, "failed": 0, "skipped": 0}
            for display_name, state_name, enabled, factory in destinations:
                destination_state = state["destinations"][state_name]
                if not enabled:
                    logger.info("%s is not configured — skipping", display_name)
                    continue
                if not _has_destination_work(
                    destination_state, current_books, pending_finished
                ):
                    logger.info(
                        "%s has no pending changes — skipping browser login",
                        display_name,
                    )
                    continue
                enabled_count += 1
                destination_attempt = time.time()
                destination_succeeded = destination_failed = 0
                try:
                    with factory() as adapter:
                        if not adapter.login():
                            logger.error(
                                "%s login failed; pending work will be retried",
                                display_name,
                            )
                            destination_failed = 1
                            totals["failed"] += destination_failed
                            for book_id, book in current_books.items():
                                if not _has_source_progress(book):
                                    continue
                                _record_failure(
                                    destination_state,
                                    book_id,
                                    "login failed",
                                    True,
                                    progress_signature(book),
                                )
                            for book_id, book in pending_finished.items():
                                _record_failure(
                                    destination_state,
                                    book_id,
                                    "login failed",
                                    True,
                                    finished_signature(book),
                                )
                        else:
                            counts = _sync_destination(
                                display_name,
                                adapter,
                                destination_state,
                                current_books,
                                pending_finished,
                                checkpoint=lambda: _save_checkpoint(
                                    config.state_file, state
                                ),
                                cancel_event=cancel_event,
                            )
                            for key, value in zip(
                                ("succeeded", "failed", "skipped"), counts
                            ):
                                totals[key] += value
                            destination_succeeded, destination_failed = counts[:2]
                except Exception as exc:
                    logger.exception("%s session failed: %s", display_name, exc)
                    totals["failed"] += 1
                    destination_failed = max(destination_failed, 1)
                health_entry = (
                    state.setdefault("health", {})
                    .setdefault("destinations", {})
                    .setdefault(state_name, {})
                )
                health_entry["last_attempt"] = destination_attempt
                health_entry["pending"] = _pending_destination_count(
                    destination_state, current_books, pending_finished
                )
                health_entry["failed"] = destination_failed
                if destination_succeeded:
                    health_entry["last_verified_success"] = time.time()
                # Checkpoint before constructing the next browser session.
                _save_checkpoint(config.state_file, state)

            enabled_names = [
                state_name for _, state_name, enabled, _ in destinations if enabled
            ]
            # Keep the source completion record after a successful destination
            # write.  Its read-scoped signature prevents repeat mutations and
            # lets the next source status response confirm or retract it.
            _save_checkpoint(config.state_file, state)

            success = totals["failed"] == 0
            reason = ""
            if configured_count == 0:
                logger.error(
                    "No destinations are configured; no remote sync was performed"
                )
                success = False
                reason = "no destinations configured"
            if any(
                _has_blocked_work(
                    state["destinations"][name], current_books, pending_finished
                )
                for _, name, enabled, _ in destinations
                if enabled
            ):
                success = False
                reason = reason or "destination action requires attention"
            if (
                enabled_names
                and enabled_count == 0
                and (
                    _has_destination_work(
                        state["destinations"][enabled_names[0]],
                        current_books,
                        pending_finished,
                    )
                    if enabled_names
                    else False
                )
            ):
                success = False
            logger.info(
                "Sync complete: %d succeeded, %d failed, %d unchanged",
                totals["succeeded"],
                totals["failed"],
                totals["skipped"],
            )
            return SyncRunResult(
                success,
                reason or ("destination failures" if not success else ""),
                **totals,
            )
    except StateError as exc:
        logger.error("State operation failed: %s", exc)
        return SyncRunResult(False, str(exc))


def run_auth_diagnostics(config: Config) -> bool:
    logger.info("Running authentication diagnostics (no progress will be changed)")
    success = True
    checked = 0
    for name, state_name, enabled, factory in _configured_destinations(config):
        if not enabled:
            logger.info("%s is not configured — skipped", name)
            continue
        checked += 1
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
    if checked == 0:
        success = False
    logger.info(
        "Authentication diagnostics complete: %s", "PASS" if success else "FAIL"
    )
    return success


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync Hardcover progress")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--once", action="store_true", help="run one complete sync and exit"
    )
    mode.add_argument(
        "--diagnose-auth", action="store_true", help="test destination authentication"
    )
    mode.add_argument(
        "--status", action="store_true", help="print persisted state and exit"
    )
    mode.add_argument(
        "--recover-state", action="store_true", help="restore the validated .bak state"
    )
    mode.add_argument(
        "--mappings", action="store_true", help="inspect persisted destination mappings"
    )
    mode.add_argument(
        "--mapping-set",
        nargs=3,
        metavar=("DESTINATION", "BOOK_ID", "URL"),
        help="set one validated destination mapping",
    )
    mode.add_argument(
        "--mapping-reset",
        nargs=2,
        metavar=("DESTINATION", "BOOK_ID"),
        help="clear one mapping and force a fresh match",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="reconcile source state without destination mutations",
    )
    parser.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default=None
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    if args.log_level:
        logging.getLogger().setLevel(args.log_level)
    try:
        config = load_config(
            require_api_key=not (
                args.status
                or args.recover_state
                or args.mappings
                or args.mapping_set
                or args.mapping_reset
            )
        )
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        raise SystemExit(2) from exc

    logger.info(
        "Hardcover Sync v%s starting (interval: %d min)",
        VERSION,
        config.sync_interval_seconds // 60,
    )
    if args.status:
        try:
            import json

            print(json.dumps(load_state(config.state_file), indent=2, sort_keys=True))
        except StateError as exc:
            logger.error("Could not read state: %s", exc)
            raise SystemExit(1) from exc
        return
    if args.recover_state:
        try:
            logger.info("%s", recover_state(config.state_file))
        except StateError as exc:
            logger.error("State recovery failed: %s", exc)
            raise SystemExit(1) from exc
        return
    if args.mappings or args.mapping_set or args.mapping_reset:
        try:
            with state_lock(config.state_file):
                state = load_state(config.state_file)
                if args.mappings:
                    import json

                    print(
                        json.dumps(
                            {
                                name: data["mappings"]
                                for name, data in state["destinations"].items()
                            },
                            indent=2,
                            sort_keys=True,
                        )
                    )
                    return
                destination, book_id = (args.mapping_set or args.mapping_reset)[:2]
                if destination not in state["destinations"]:
                    raise StateError(f"Unknown destination: {destination}")
                dest = state["destinations"][destination]
                if args.mapping_set:
                    target = canonical_book_url(destination, args.mapping_set[2])
                    if not target:
                        raise StateError(
                            "Mapping URL is not a valid HTTPS provider book URL"
                        )
                    dest["mappings"][book_id] = target
                    dest["books"].pop(book_id, None)
                else:
                    dest["mappings"].pop(book_id, None)
                    dest["books"].pop(book_id, None)
                save_state(config.state_file, state)
                logger.info("Mapping updated for %s/%s", destination, book_id)
        except StateError as exc:
            logger.error("Mapping operation failed: %s", exc)
            raise SystemExit(1) from exc
        return
    if args.diagnose_auth:
        raise SystemExit(0 if run_auth_diagnostics(config) else 1)
    if args.once:
        outcome = run_sync(config, dry_run=args.dry_run)
        if not outcome.success:
            raise SystemExit(1)
        logger.info("One-shot sync complete")
        return

    running = True

    def _stop(_sig, _frame):
        nonlocal running
        logger.info("Shutdown signal received")
        running = False
        STOP_EVENT.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while running:
        try:
            run_sync(config, dry_run=args.dry_run, cancel_event=STOP_EVENT)
        except Exception as exc:
            logger.exception("Unexpected error during sync: %s", exc)
        if not running:
            break
        logger.info("Next sync in %d minutes", config.sync_interval_seconds // 60)
        for _ in range(config.sync_interval_seconds):
            if not running:
                break
            time.sleep(1)
    logger.info("Hardcover Sync stopped")


if __name__ == "__main__":
    main()
