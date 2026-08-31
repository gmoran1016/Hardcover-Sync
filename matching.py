"""Conservative book-result matching helpers."""

from __future__ import annotations

from difflib import SequenceMatcher
import re
import unicodedata


def normalise(text: str) -> str:
    value = unicodedata.normalize("NFKD", text or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.casefold()
    value = re.sub(r"\([^)]*\)", " ", value)
    value = re.sub(r"[\W_]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def title_score(expected: str, candidate: str) -> float:
    left = normalise(expected)
    right = normalise(candidate)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left in right or right in left:
        shorter, longer = sorted((len(left), len(right)))
        return 0.88 + 0.1 * (shorter / longer)
    return SequenceMatcher(None, left, right).ratio()


def candidate_title(candidate_text: str) -> str:
    """Return the first nonempty line from a destination search result."""
    return next(
        (line.strip() for line in candidate_text.splitlines() if line.strip()),
        "",
    )


def result_score(title: str, author: str | None, candidate_text: str) -> float:
    title_component = max(
        (
            title_score(title, line.strip())
            for line in candidate_text.splitlines()
            if line.strip()
        ),
        default=0.0,
    )
    author_norm = normalise(author or "")
    candidate_norm = normalise(candidate_text)
    if not author_norm or author_norm == "unknown":
        return title_component
    author_component = 1.0 if author_norm in candidate_norm else 0.0
    return 0.92 * title_component + 0.08 * author_component


def choose_match(
    title: str,
    author: str | None,
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
