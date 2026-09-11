"""Versioned, validated and atomic synchronization state persistence."""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import tempfile
import time
import uuid

logger = logging.getLogger(__name__)
SCHEMA_VERSION = 2


class StateError(RuntimeError):
    """Raised when state cannot be read, validated, locked, or saved safely."""


def empty_state() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "source_books": {},
        "pending_finished": {},
        "health": {},
        "source_retry_at": None,
        "destinations": {
            "goodreads": {"books": {}, "mappings": {}, "failures": {}},
            "storygraph": {"books": {}, "mappings": {}, "failures": {}},
        },
    }


def _mapping(value, field: str) -> dict:
    if not isinstance(value, dict):
        raise StateError(f"Sync state field '{field}' must be an object")
    return dict(value)


def _finite_number(
    value, field: str, *, minimum: float | None = None, maximum: float | None = None
) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StateError(f"Sync state field '{field}' must be a number")
    if not math.isfinite(float(value)):
        raise StateError(f"Sync state field '{field}' must be finite")
    if minimum is not None and value < minimum:
        raise StateError(f"Sync state field '{field}' is below its minimum")
    if maximum is not None and value > maximum:
        raise StateError(f"Sync state field '{field}' is above its maximum")


def _validate_book(record, field: str, record_key: str | None = None) -> dict:
    if not isinstance(record, dict):
        raise StateError(f"Sync state field '{field}' contains a non-object book")
    book = dict(record)
    title = book.get("title")
    if not isinstance(title, str) or not title.strip():
        raise StateError(f"Sync state field '{field}' contains an invalid title")
    if "author" in book and not isinstance(book["author"], str):
        raise StateError(f"Sync state field '{field}' contains an invalid author")
    for field_key in ("id", "user_book_id", "book_id", "edition_id", "read_id"):
        if field_key in book and book[field_key] is not None:
            value = book[field_key]
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                raise StateError(
                    f"Sync state field '{field}' contains an invalid {field_key}"
                )
            if field_key == "user_book_id":
                try:
                    int(value)
                except (TypeError, ValueError):
                    raise StateError(
                        f"Sync state field '{field}' contains an invalid {field_key}"
                    )
            if (
                field_key == "id"
                and record_key is not None
                and str(value) != str(record_key)
            ):
                raise StateError(
                    f"Sync state field '{field}' contains an id that does not match its key"
                )
    for key in ("progress_pages", "total_pages"):
        if key in book and book[key] is not None:
            _finite_number(book[key], f"{field}.{key}", minimum=0)
    if "progress_percent" in book and book["progress_percent"] is not None:
        _finite_number(
            book["progress_percent"],
            f"{field}.progress_percent",
            minimum=0,
            maximum=100,
        )
    if "status_id" in book and book["status_id"] is not None:
        if isinstance(book["status_id"], bool) or not isinstance(
            book["status_id"], int
        ):
            raise StateError(
                f"Sync state field '{field}' contains an invalid status_id"
            )
    return book


def _book_collection(value, field: str) -> dict:
    collection = _mapping(value, field)
    validated = {}
    for key, record in collection.items():
        if not isinstance(key, str) or not key:
            raise StateError(f"Sync state field '{field}' contains an invalid key")
        validated[key] = _validate_book(record, f"{field}.{key}", key)
    return validated


def _string_map(value, field: str) -> dict:
    collection = _mapping(value, field)
    validated = {}
    for key, item in collection.items():
        if not isinstance(key, str) or not isinstance(item, str) or not item:
            raise StateError(f"Sync state field '{field}' contains an invalid value")
        validated[key] = item
    return validated


def _validate_failures(value, field: str) -> dict:
    collection = _mapping(value, field)
    validated = {}
    for key, failure in collection.items():
        if not isinstance(key, str) or not isinstance(failure, dict):
            raise StateError(f"Sync state field '{field}' contains an invalid failure")
        reason = failure.get("reason")
        retryable = failure.get("retryable")
        updated_at = failure.get("updated_at")
        if not isinstance(reason, str) or not isinstance(retryable, bool):
            raise StateError(
                f"Sync state field '{field}' contains invalid failure metadata"
            )
        _finite_number(updated_at, f"{field}.{key}.updated_at", minimum=0)
        for optional in ("signature", "target_url"):
            if (
                optional in failure
                and failure[optional] is not None
                and not isinstance(failure[optional], str)
            ):
                raise StateError(
                    f"Sync state field '{field}.{key}.{optional}' must be a string"
                )
        validated[key] = dict(failure)
    return validated


def _normalize_v2(data: dict) -> dict:
    state = empty_state()
    state["source_books"] = _book_collection(
        data.get("source_books", {}), "source_books"
    )
    state["pending_finished"] = _book_collection(
        data.get("pending_finished", {}), "pending_finished"
    )
    health = data.get("health", {})
    if not isinstance(health, dict):
        raise StateError("Sync state field 'health' must be an object")
    state["health"] = dict(health)
    retry_at = data.get("source_retry_at")
    if retry_at is not None:
        _finite_number(retry_at, "source_retry_at", minimum=0)
    state["source_retry_at"] = retry_at

    destinations = _mapping(data.get("destinations", {}), "destinations")
    for name in ("goodreads", "storygraph"):
        destination = _mapping(destinations.get(name, {}), f"destinations.{name}")
        state["destinations"][name] = {
            "books": _string_map(
                destination.get("books", {}), f"destinations.{name}.books"
            ),
            "mappings": _string_map(
                destination.get("mappings", {}), f"destinations.{name}.mappings"
            ),
            "failures": _validate_failures(
                destination.get("failures", {}), f"destinations.{name}.failures"
            ),
        }
    return state


def _migrate_legacy(data: dict) -> dict:
    books = data.get("books")
    if not isinstance(books, list):
        raise StateError(
            "Sync state has no supported schema version or legacy books list"
        )
    state = empty_state()
    for index, book in enumerate(books):
        if not isinstance(book, dict):
            raise StateError("Legacy sync state contains a non-object book")
        migrated = _validate_book(book, f"legacy.books[{index}]")
        key = str(
            migrated.get("user_book_id")
            or migrated.get("id")
            or f"legacy:{index}:{migrated.get('title', '')}"
        )
        migrated["id"] = key
        state["source_books"][key] = migrated
    return state


def _read_file(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"Sync state is unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise StateError("Sync state must be a top-level object")
    version = data.get("schema_version")
    if version == SCHEMA_VERSION and not isinstance(version, bool):
        return _normalize_v2(data)
    if version == 1 and not isinstance(version, bool):
        return _migrate_legacy(data)
    if "schema_version" not in data and isinstance(data.get("books"), list):
        return _migrate_legacy(data)
    raise StateError(f"Unsupported sync state schema version: {version!r}")


def load_state(path: str) -> dict:
    if not os.path.exists(path):
        if os.path.exists(f"{path}.bak"):
            raise StateError(
                "Primary sync state is missing; recover the backup explicitly"
            )
        return empty_state()
    return _read_file(path)


class _StateLock:
    def __init__(self, path: str):
        self.path = f"{path}.lock"
        self.handle = None

    def __enter__(self):
        directory = os.path.dirname(self.path) or "."
        try:
            os.makedirs(directory, exist_ok=True)
            self.handle = open(self.path, "a+b")
            self.handle.seek(0, os.SEEK_END)
            if self.handle.tell() == 0:
                self.handle.write(b"0")
                self.handle.flush()
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self
        except (OSError, BlockingIOError) as exc:
            if self.handle is not None:
                try:
                    self.handle.close()
                except OSError:
                    pass
                self.handle = None
            raise StateError(
                f"Sync state is already in use or cannot be locked: {exc}"
            ) from exc

    def __exit__(self, *_):
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def state_lock(path: str):
    """Return an inter-process lock; the lock file is safe to leave behind."""
    return _StateLock(path)


def _validate_state(state: dict) -> dict:
    if not isinstance(state, dict):
        raise StateError("Sync state must be an object")
    normalized = dict(state)
    normalized["schema_version"] = SCHEMA_VERSION
    normalized["source_books"] = _book_collection(
        state.get("source_books", {}), "source_books"
    )
    normalized["pending_finished"] = _book_collection(
        state.get("pending_finished", {}), "pending_finished"
    )
    health = state.get("health", {})
    if not isinstance(health, dict):
        raise StateError("Sync state field 'health' must be an object")
    normalized["health"] = dict(health)
    retry_at = state.get("source_retry_at")
    if retry_at is not None:
        _finite_number(retry_at, "source_retry_at", minimum=0)
    normalized["source_retry_at"] = retry_at
    destinations = _mapping(state.get("destinations", {}), "destinations")
    normalized["destinations"] = {}
    for name in ("goodreads", "storygraph"):
        dest = _mapping(destinations.get(name, {}), f"destinations.{name}")
        normalized["destinations"][name] = {
            "books": _string_map(dest.get("books", {}), f"destinations.{name}.books"),
            "mappings": _string_map(
                dest.get("mappings", {}), f"destinations.{name}.mappings"
            ),
            "failures": _validate_failures(
                dest.get("failures", {}), f"destinations.{name}.failures"
            ),
        }
    return normalized


def _write_atomic(path: str, state: dict) -> None:
    directory = os.path.dirname(path) or "."
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(
            prefix=".sync_state.", suffix=".tmp", dir=directory
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def save_state(path: str, state: dict) -> None:
    """Validate and atomically save state, retaining a known-good backup."""
    normalized = _validate_state(state)
    directory = os.path.dirname(path) or "."
    try:
        os.makedirs(directory, exist_ok=True)
        if os.path.exists(path):
            _read_file(path)
        backup = f"{path}.bak"
        if os.path.exists(backup):
            _read_file(backup)
        temp_path = None
        fd, temp_path = tempfile.mkstemp(
            prefix=".sync_state.", suffix=".tmp", dir=directory
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(normalized, handle, indent=2, sort_keys=True, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            if os.path.exists(path):
                shutil.copy2(path, backup)
            os.replace(temp_path, path)
            temp_path = None
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
    except StateError:
        raise
    except OSError as exc:
        logger.error("Could not save sync state atomically: %s", exc)
        raise StateError(f"Could not save sync state: {exc}") from exc


def probe_state_storage(path: str) -> None:
    """Verify directory create/write/replace capability without touching state."""
    directory = os.path.dirname(path) or "."
    temp_path = None
    probe_path = None
    try:
        os.makedirs(directory, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(
            prefix=".sync_state_probe.", suffix=".tmp", dir=directory
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("probe")
            handle.flush()
            os.fsync(handle.fileno())
        probe_path = f"{temp_path}.replace"
        os.replace(temp_path, probe_path)
        temp_path = None
    except OSError as exc:
        raise StateError(f"Sync state storage probe failed: {exc}") from exc
    finally:
        for candidate in (temp_path, probe_path):
            if candidate and os.path.exists(candidate):
                try:
                    os.unlink(candidate)
                except OSError:
                    pass


def recover_state(path: str) -> str:
    """Restore a validated backup, preserving any damaged primary as evidence."""
    backup = f"{path}.bak"
    with state_lock(path):
        if not os.path.exists(backup):
            raise StateError("No sync state backup exists to recover")
        recovered = _read_file(backup)
        preserved = None
        if os.path.exists(path):
            preserved = f"{path}.corrupt.{int(time.time())}.{uuid.uuid4().hex[:8]}"
            os.replace(path, preserved)
        try:
            _write_atomic(path, recovered)
        except Exception:
            if preserved and not os.path.exists(path) and os.path.exists(preserved):
                os.replace(preserved, path)
            raise
        if preserved:
            return preserved
        return "Recovered backup; primary was missing"


def progress_signature(book: dict) -> str:
    """Include read and edition identity so rereads never inherit old progress."""
    return "progress:" + ":".join(
        str(book.get(field))
        for field in (
            "read_id",
            "edition_id",
            "total_pages",
            "progress_pages",
            "progress_percent",
        )
    )


def finished_signature(book: dict) -> str:
    """Return a completion marker scoped to the specific Hardcover read."""
    identity = book.get("read_id")
    return f"finished:{identity}" if identity is not None else "finished"
