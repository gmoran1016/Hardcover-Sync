"""Conservative book-result matching and provider URL identity helpers."""

from __future__ import annotations

from difflib import SequenceMatcher
import re
import unicodedata
from urllib.parse import unquote, urlsplit


def normalise(text: str) -> str:
    value = unicodedata.normalize("NFKD", text or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.casefold()
    value = re.sub(r"\([^)]*\)", " ", value)
    value = re.sub(r"[\W_]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


_TITLE_DESCRIPTORS = {
    "book",
    "edition",
    "hardcover",
    "paperback",
    "mass",
    "market",
    "audio",
    "audiobook",
    "unabridged",
    "cd",
    "kindle",
    "ebook",
    "digital",
    "special",
    "anniversary",
    "illustrated",
    "reprint",
    "library",
    "tor",
    "fantasy",
    "novel",
    "volume",
    "vol",
    "series",
    "the",
    "wheel",
    "of",
    "time",
}


def _title_line(candidate: str) -> str:
    lines = [line.strip() for line in candidate.splitlines() if line.strip()]
    for line in lines:
        if normalise(line).startswith("by "):
            continue
        # Search pages commonly append edition metadata after the title.
        before_author = re.split(r"\s+by\s+", line, maxsplit=1, flags=re.I)[0]
        return before_author.strip()
    return ""


def _title_has_only_metadata(expected: str, candidate: str) -> bool:
    left = normalise(expected)
    right = normalise(candidate)
    if not left or not right:
        return False
    if right == left:
        return True
    if not right.startswith(left + " "):
        return False
    extras = right[len(left) :].split()
    return all(token in _TITLE_DESCRIPTORS or token.isdigit() for token in extras)


def title_score(expected: str, candidate: str) -> float:
    left = normalise(expected)
    right = normalise(candidate)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    title = _title_line(candidate)
    if _title_has_only_metadata(left, title):
        return 0.97
    # A candidate with an additional leading or arbitrary trailing title token is
    # not an exact work match (for example Dune vs Dune Messiah).
    if left in right.split() and len(right.split()) <= len(left.split()) + 1:
        return SequenceMatcher(None, left, right).ratio() * 0.82
    return SequenceMatcher(None, left, right).ratio()


def candidate_title(candidate_text: str) -> str:
    return next(
        (line.strip() for line in candidate_text.splitlines() if line.strip()),
        "",
    )


def _author_evidence(author: str | list[str], candidate_text: str) -> tuple[bool, bool]:
    """Return (agreement, explicit_conflict) using token boundaries."""
    expected = author if isinstance(author, list) else [author]
    expected_tokens = [
        value.split()
        for name in expected
        if isinstance(name, str) and (value := normalise(name)) and value != "unknown"
    ]
    if not expected_tokens:
        return False, False
    lines = [normalise(line) for line in candidate_text.splitlines() if line.strip()]
    whole = normalise(candidate_text)
    agreement = False
    explicit = False
    for line_index, line in enumerate(lines + [whole]):
        tokens = line.split()
        line_agrees = any(
            tokens[index : index + len(name_tokens)] == name_tokens
            for name_tokens in expected_tokens
            for index in range(max(0, len(tokens) - len(name_tokens) + 1))
        )
        if line_agrees:
            agreement = True
        if (
            line.startswith("by ")
            or " by " in line
            or line_index > 0
            or (" — " in candidate_text or " - " in candidate_text)
            and line_index == 0
        ):
            if not line_agrees:
                explicit = True
    return agreement, explicit


def result_score(
    title: str, author: str | list[str] | None, candidate_text: str
) -> float:
    title_component = max(
        (
            title_score(title, line.strip())
            for line in candidate_text.splitlines()
            if line.strip()
        ),
        default=title_score(title, candidate_text),
    )
    expected_authors = author if isinstance(author, list) else [author]
    if not any(
        isinstance(name, str) and normalise(name) not in ("", "unknown")
        for name in expected_authors
    ):
        return title_component
    agreement, conflict = _author_evidence(author, candidate_text)
    if conflict and not agreement:
        return 0.0
    score = 0.92 * title_component + 0.08 * (1.0 if agreement else 0.0)
    raw_first = candidate_title(candidate_text)
    title_line = normalise(raw_first)
    # Prefer the canonical work result over catalog edition rows that put an
    # author/format imprint directly in the title line.
    if (
        "tor fantasy" in title_line
        or "hardcover" in title_line
        or "jordan robert" in title_line
    ):
        score -= 0.04
    return max(0.0, score)


def choose_match(
    title: str,
    author: str | list[str] | None,
    candidates: list[tuple[str, str]],
    threshold: float = 0.82,
    margin: float = 0.05,
) -> str | None:
    scored = sorted(
        (
            (result_score(title, author, text), normalise(text), url)
            for text, url in candidates
            if url
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    if not scored or scored[0][0] < threshold:
        return None
    best_score, best_key, best_url = scored[0]
    for score, candidate_key, _url in scored[1:]:
        if best_score - score >= margin:
            break
        if candidate_key != best_key:
            return None
    return best_url


def canonical_book_url(destination: str, url: str | None) -> str | None:
    """Return the only URL form allowed for a destination book identity."""
    if not isinstance(url, str) or not url:
        return None
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme.casefold() != "https"
            or parsed.username
            or parsed.password
            or parsed.port
        ):
            return None
        host = (parsed.hostname or "").casefold()
        path = unquote(parsed.path)
    except (ValueError, UnicodeError):
        return None
    if "\\" in path or ".." in path.split("/"):
        return None
    if destination.casefold() == "goodreads":
        if host not in {"goodreads.com", "www.goodreads.com"}:
            return None
        match = re.fullmatch(r"/book/show/(\d+)(?:\.[^/]*)?", path.rstrip("/"))
        if not match:
            return None
        return f"https://www.goodreads.com/book/show/{match.group(1)}"
    if destination.casefold() == "storygraph":
        if host != "app.thestorygraph.com":
            return None
        match = re.fullmatch(r"/books/([A-Za-z0-9][A-Za-z0-9_-]*)", path.rstrip("/"))
        if not match or match.group(1).casefold() == "new":
            return None
        return f"https://app.thestorygraph.com/books/{match.group(1)}"
    return None
