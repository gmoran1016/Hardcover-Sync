"""Hardcover GraphQL client with explicit failure semantics."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)
GRAPHQL_URL = "https://api.hardcover.app/v1/graphql"


class HardcoverAPIError(RuntimeError):
    """Raised when Hardcover cannot provide a trustworthy response."""


class HardcoverDeferredError(HardcoverAPIError):
    """Raised for a rate-limit response that should be retried later."""

    def __init__(self, message: str, retry_after_seconds: float = 60.0):
        super().__init__(message)
        # Preserve the provider's requested cooldown as state; never sleep for
        # it inside the HTTP request itself.
        self.retry_after_seconds = max(1.0, float(retry_after_seconds))


CURRENT_QUERY = """
query GetCurrentlyReading {
  me {
    user_books(where: {status_id: {_eq: 2}}) {
      id
      status_id
      book {
        id
        title
        pages
        contributions {
          author { name }
        }
      }
      user_book_reads(order_by: {id: desc}, limit: 1) {
        id
        progress
        progress_pages
        edition {
          id
          pages
        }
      }
    }
  }
}
"""

STATUS_QUERY = """
query GetBookStatuses($ids: [Int!]!) {
  me {
    user_books(where: {id: {_in: $ids}}) {
      id
      status_id
      book {
        id
        title
        pages
        contributions {
          author { name }
        }
      }
      user_book_reads(order_by: {id: desc}, limit: 1) { id edition { id pages } }
    }
  }
}
"""


def _session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=0,
        backoff_factor=0.2,
        backoff_max=30,
        status_forcelist=(),
        allowed_methods=frozenset({"POST"}),
        respect_retry_after_header=False,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _request(api_key: str, query: str, variables: dict | None = None) -> dict:
    token = api_key.removeprefix("Bearer ").strip()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        response = _session().post(
            GRAPHQL_URL,
            json={"query": query, "variables": variables or {}},
            headers=headers,
            timeout=(10, 30),
        )
        if response.status_code in (429, 503):
            retry_after = response.headers.get("Retry-After")
            delay = 60.0
            if retry_after:
                try:
                    delay = float(retry_after)
                except (TypeError, ValueError):
                    try:
                        when = parsedate_to_datetime(retry_after)
                        if when.tzinfo is None:
                            when = when.replace(tzinfo=timezone.utc)
                        delay = max(
                            1.0, (when - datetime.now(timezone.utc)).total_seconds()
                        )
                    except (TypeError, ValueError, OverflowError):
                        delay = 60.0
            raise HardcoverDeferredError(
                f"Hardcover deferred the request with HTTP {response.status_code}",
                delay,
            )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise HardcoverAPIError(f"Hardcover request failed: {exc}") from exc

    if not isinstance(payload, dict):
        raise HardcoverAPIError("Hardcover did not return a JSON object")
    if payload.get("errors"):
        raise HardcoverAPIError(f"Hardcover GraphQL errors: {payload['errors']}")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("me"), list):
        raise HardcoverAPIError("Hardcover returned an unexpected response shape")
    return data


def _authors(book: dict) -> list[str]:
    contributions = book.get("contributions")
    if contributions is None:
        contributions = []
    if not isinstance(contributions, list) or any(
        not isinstance(contribution, dict) for contribution in contributions
    ):
        raise HardcoverAPIError("Hardcover returned malformed book contributions")
    authors = []
    seen = set()
    for contribution in contributions:
        author = contribution.get("author")
        if author is None:
            author = {}
        if not isinstance(author, dict):
            raise HardcoverAPIError("Hardcover returned a malformed author object")
        name = author.get("name")
        if name is None:
            continue
        if not isinstance(name, str) or not name.strip():
            raise HardcoverAPIError("Hardcover returned a malformed author name")
        name = name.strip()
        if name.casefold() not in seen:
            authors.append(name)
            seen.add(name.casefold())
    return authors


def _number(value, field: str, *, maximum: float | None = None):
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise HardcoverAPIError(f"Hardcover returned an invalid {field}")
    if value < 0 or (maximum is not None and value > maximum):
        raise HardcoverAPIError(f"Hardcover returned an invalid {field}")
    return value


def _identifier(value, field: str, *, required: bool = True):
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise HardcoverAPIError(f"Hardcover returned an invalid {field}")
    return value


def _book_entry(user_book: dict) -> dict:
    if not isinstance(user_book, dict):
        raise HardcoverAPIError("Hardcover returned a malformed book entry")
    book = user_book.get("book")
    if book is None:
        book = {}
    if not isinstance(book, dict):
        raise HardcoverAPIError("Hardcover returned a malformed book object")
    reads = user_book.get("user_book_reads")
    if reads is None:
        reads = []
    if not isinstance(reads, list) or any(not isinstance(read, dict) for read in reads):
        raise HardcoverAPIError("Hardcover returned malformed reading progress")
    latest = reads[0] if reads else {}
    edition = latest.get("edition")
    if edition is None:
        edition = {}
    if not isinstance(edition, dict):
        raise HardcoverAPIError("Hardcover returned a malformed edition object")
    user_book_id = _identifier(user_book.get("id"), "user book id")
    book_id = _identifier(book.get("id"), "book id")
    title = book.get("title")
    if not isinstance(title, str) or not title.strip():
        raise HardcoverAPIError("Hardcover returned a book without stable IDs or title")
    status_id = _identifier(user_book.get("status_id"), "status id", required=False)
    read_id = _identifier(latest.get("id"), "read id", required=False)
    edition_id = _identifier(edition.get("id"), "edition id", required=False)
    total_pages = _number(edition.get("pages") or book.get("pages"), "total page count")
    progress_pages = _number(latest.get("progress_pages"), "progress page count")
    progress_percent = _number(
        latest.get("progress"), "progress percentage", maximum=100
    )
    authors = _authors(book)
    return {
        "id": str(user_book_id),
        "user_book_id": user_book_id,
        "book_id": book_id,
        "edition_id": edition_id,
        "read_id": read_id,
        "status_id": status_id,
        "title": title.strip(),
        "author": authors[0] if authors else "Unknown",
        "authors": authors,
        "total_pages": total_pages,
        "progress_pages": progress_pages,
        "progress_percent": progress_percent,
    }


def get_currently_reading(api_key: str) -> list[dict]:
    data = _request(api_key, CURRENT_QUERY)
    me = data["me"][0] if data["me"] else {}
    if not isinstance(me, dict):
        raise HardcoverAPIError("Hardcover returned a malformed account entry")
    user_books = me.get("user_books")
    if not isinstance(user_books, list):
        raise HardcoverAPIError("Hardcover response omitted user_books")
    books = [_book_entry(item) for item in user_books]
    if len({entry["id"] for entry in books}) != len(books):
        raise HardcoverAPIError("Hardcover returned duplicate user book ids")
    for entry in books:
        logger.info(
            "Hardcover: '%s' by %s — %s/%s pages (%.1f%%)",
            entry["title"],
            entry["author"],
            entry["progress_pages"] if entry["progress_pages"] is not None else "?",
            entry["total_pages"] if entry["total_pages"] is not None else "?",
            entry["progress_percent"] or 0.0,
        )
    return books


def get_book_statuses(api_key: str, ids: list[int]) -> dict[str, dict]:
    if not ids:
        return {}
    data = _request(api_key, STATUS_QUERY, {"ids": ids})
    me = data["me"][0] if data["me"] else {}
    if not isinstance(me, dict):
        raise HardcoverAPIError("Hardcover returned a malformed account entry")
    user_books = me.get("user_books")
    if not isinstance(user_books, list):
        raise HardcoverAPIError("Hardcover status response omitted user_books")

    statuses = {}
    for user_book in user_books:
        if not isinstance(user_book, dict):
            raise HardcoverAPIError("Hardcover returned a malformed book entry")
        book = user_book.get("book")
        if book is None:
            book = {}
        if not isinstance(book, dict):
            raise HardcoverAPIError("Hardcover returned a malformed book object")
        user_book_id = user_book.get("id")
        book_id = book.get("id")
        title = book.get("title")
        try:
            user_book_id = _identifier(user_book_id, "user book id")
            book_id = _identifier(book_id, "book id")
        except HardcoverAPIError as exc:
            raise HardcoverAPIError(
                "Hardcover returned a book without stable IDs or title"
            ) from exc
        if not isinstance(title, str) or not title.strip():
            raise HardcoverAPIError(
                "Hardcover returned a book without stable IDs or title"
            )
        key = str(user_book_id)
        if key in statuses:
            raise HardcoverAPIError("Hardcover returned duplicate status records")
        status_id = user_book.get("status_id")
        status_id = _identifier(status_id, "status id", required=False)
        reads = user_book.get("user_book_reads") or []
        if not isinstance(reads, list) or any(
            not isinstance(read, dict) for read in reads
        ):
            raise HardcoverAPIError("Hardcover returned malformed reading progress")
        latest = reads[0] if reads else {}
        edition = latest.get("edition") or {}
        if not isinstance(edition, dict):
            raise HardcoverAPIError("Hardcover returned a malformed edition object")
        read_id = _identifier(latest.get("id"), "read id", required=False)
        edition_id = _identifier(edition.get("id"), "edition id", required=False)
        total_pages = _number(
            edition.get("pages") or book.get("pages"), "total page count"
        )
        authors = _authors(book)
        statuses[key] = {
            "id": key,
            "user_book_id": user_book_id,
            "book_id": book_id,
            "title": title.strip(),
            "author": authors[0] if authors else "Unknown",
            "authors": authors,
            "total_pages": total_pages,
            "status_id": status_id,
            "read_id": read_id,
            "edition_id": edition_id,
        }
    return statuses
