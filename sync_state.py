"""Versioned, atomic persistence for synchronization state."""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile

logger = logging.getLogger(__name__)
SCHEMA_VERSION = 2


class StateError(RuntimeError):
    """Raised when existing state cannot be read safely."""


def empty_state() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "source_books": {},
        "pending_finished": {},
        "destinations": {
            "goodreads": {"books": {}, "mappings": {}},
            "storygraph": {"books": {}, "mappings": {}},
        },
    }


def _migrate_legacy(data: dict) -> dict:
    state = empty_state()
    for index, book in enumerate(data.get("books", [])):
        key = str(book.get("user_book_id") or f"legacy:{index}:{book.get('title', '')}")
        migrated = dict(book)
        migrated["id"] = key
        state["source_books"][key] = migrated
    return state


def _mapping(value, field: str) -> dict:
    if not isinstance(value, dict):
        raise StateError(f"Sync state field '{field}' must be an object")
    return dict(value)


def _normalize_v2(data: dict) -> dict:
    state = empty_state()
    state["source_books"] = _mapping(data.get("source_books", {}), "source_books")
    state["pending_finished"] = _mapping(
        data.get("pending_finished", {}), "pending_finished"
    )
    destinations = _mapping(data.get("destinations", {}), "destinations")
    for name in ("goodreads", "storygraph"):
        destination = _mapping(destinations.get(name, {}), f"destinations.{name}")
        state["destinations"][name] = {
            "books": _mapping(
                destination.get("books", {}), f"destinations.{name}.books"
            ),
            "mappings": _mapping(
                destination.get("mappings", {}), f"destinations.{name}.mappings"
            ),
        }
    return state


def load_state(path: str) -> dict:
    if not os.path.exists(path):
        return empty_state()
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"Sync state is unreadable: {exc}") from exc

    if not isinstance(data, dict):
        raise StateError("Sync state must be a top-level object")

    if data.get("schema_version") != SCHEMA_VERSION:
        logger.info("Migrating legacy sync state to schema v%d", SCHEMA_VERSION)
        return _migrate_legacy(data)

    return _normalize_v2(data)


def save_state(path: str, state: dict) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(
            prefix=".sync_state.", suffix=".tmp", dir=directory
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        if os.path.exists(path):
            shutil.copy2(path, f"{path}.bak")
        os.replace(temp_path, path)
    except OSError as exc:
        logger.error("Could not save sync state atomically: %s", exc)
        raise
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def progress_signature(book: dict) -> str:
    return f"{book.get('progress_pages')}:{book.get('progress_percent')}"
