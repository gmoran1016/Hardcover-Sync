import logging
import requests

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.hardcover.app/v1/graphql"

# status_id: 1=Want to Read, 2=Currently Reading, 3=Read, 4=Paused, 5=Did Not Finish
QUERY = """
query GetCurrentlyReading {
  me {
    user_books(where: {status_id: {_eq: 2}}) {
      book {
        title
        pages
        contributions {
          author {
            name
          }
        }
      }
      user_book_reads(order_by: {started_at: desc_nulls_last}, limit: 1) {
        progress
        progress_pages
        edition {
          pages
        }
      }
    }
  }
}
"""


def get_currently_reading(api_key: str) -> list[dict]:
    """Fetch currently-reading books from Hardcover via GraphQL.

    Returns a list of dicts with keys:
      title, author, total_pages, progress_pages, progress_percent
    """
    # Strip a "Bearer " prefix if the user stored the full header value in .env
    token = api_key.removeprefix("Bearer ").strip()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(
            GRAPHQL_URL,
            json={"query": QUERY},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        if "errors" in data:
            logger.error("Hardcover API returned errors: %s", data["errors"])
            return []

        me_data = (data.get("data") or {}).get("me") or []
        # me is returned as a list; take the first (and only) element
        me = me_data[0] if me_data else {}
        user_books = me.get("user_books", [])

        books = []
        for ub in user_books:
            book = ub.get("book") or {}
            reads = ub.get("user_book_reads") or []
            latest = reads[0] if reads else {}
            edition = latest.get("edition") or {}

            # Prefer edition pages (the specific copy being read) over book default
            total_pages = edition.get("pages") or book.get("pages")
            progress_pages = latest.get("progress_pages")
            progress_pct = latest.get("progress")  # float 0–100

            # Extract primary author from contributions list
            author = "Unknown"
            contribs = book.get("contributions") or []
            if contribs:
                author_obj = (contribs[0].get("author")) or {}
                author = author_obj.get("name", "Unknown")

            entry = {
                "title": book.get("title", "Unknown"),
                "author": author,
                "total_pages": total_pages,
                "progress_pages": progress_pages,
                "progress_percent": progress_pct,
            }

            logger.info(
                "Hardcover: '%s' by %s — %s/%s pages (%.1f%%)",
                entry["title"],
                entry["author"],
                progress_pages if progress_pages is not None else "?",
                total_pages if total_pages is not None else "?",
                progress_pct or 0.0,
            )
            books.append(entry)

        return books

    except requests.RequestException as exc:
        logger.error("Hardcover request failed: %s", exc)
        return []
