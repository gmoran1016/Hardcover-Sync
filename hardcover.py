"""Hardcover GraphQL client with explicit failure semantics."""

from __future__ import annotations

import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)
GRAPHQL_URL = "https://api.hardcover.app/v1/graphql"


class HardcoverAPIError(RuntimeError):
    """Raised when Hardcover cannot provide a trustworthy response."""


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
    }
  }
}
"""


def _session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"POST"}),
        respect_retry_after_header=True,
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
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise HardcoverAPIError(f"Hardcover request failed: {exc}") from exc

    if payload.get("errors"):
        raise HardcoverAPIError(f"Hardcover GraphQL errors: {payload['errors']}")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("me"), list):
        raise HardcoverAPIError("Hardcover returned an unexpected response shape")
    return data


def _author(book: dict) -> str:
    contributions = book.get("contributions") or []
    if not contributions:
        return "Unknown"
    return ((contributions[0].get("author") or {}).get("name")) or "Unknown"


def _book_entry(user_book: dict) -> dict:
    book = user_book.get("book") or {}
    reads = user_book.get("user_book_reads") or []
    latest = reads[0] if reads else {}
    edition = latest.get("edition") or {}
    user_book_id = user_book.get("id")
    if user_book_id is None or book.get("id") is None or not book.get("title"):
        raise HardcoverAPIError("Hardcover returned a book without stable IDs or title")
    return {
        "id": str(user_book_id),
        "user_book_id": user_book_id,
        "book_id": book["id"],
        "edition_id": edition.get("id"),
        "title": book["title"],
        "author": _author(book),
        "total_pages": edition.get("pages") or book.get("pages"),
        "progress_pages": latest.get("progress_pages"),
        "progress_percent": latest.get("progress"),
    }


def get_currently_reading(api_key: str) -> list[dict]:
    data = _request(api_key, CURRENT_QUERY)
    me = data["me"][0] if data["me"] else {}
    user_books = me.get("user_books")
    if not isinstance(user_books, list):
        raise HardcoverAPIError("Hardcover response omitted user_books")
    books = [_book_entry(item) for item in user_books]
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
    user_books = me.get("user_books")
    if not isinstance(user_books, list):
        raise HardcoverAPIError("Hardcover status response omitted user_books")

    statuses = {}
    for user_book in user_books:
        book = user_book.get("book") or {}
        key = str(user_book.get("id"))
        statuses[key] = {
            "id": key,
            "user_book_id": user_book.get("id"),
            "book_id": book.get("id"),
            "title": book.get("title", "Unknown"),
            "author": _author(book),
            "total_pages": book.get("pages"),
            "status_id": user_book.get("status_id"),
        }
    return statuses
