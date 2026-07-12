"""Cookie-file compatibility helpers with captured browser identity."""

from __future__ import annotations

from dataclasses import dataclass
import json


@dataclass(frozen=True)
class CookieBundle:
    cookies: list[dict]
    user_agent: str | None = None
    user_agent_metadata: dict | None = None


def _cookies(value) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("cookie file must contain a list of cookie records")
    return [dict(item) for item in value]


def decode_cookie_bundle(data) -> CookieBundle:
    """Read both legacy cookie lists and v1 identity-aware bundles."""
    if isinstance(data, list):
        return CookieBundle(cookies=_cookies(data))
    if isinstance(data, dict) and isinstance(data.get("cookies"), list):
        user_agent = data.get("user_agent")
        return CookieBundle(
            cookies=_cookies(data["cookies"]),
            user_agent=user_agent if isinstance(user_agent, str) else None,
            user_agent_metadata=(
                data.get("user_agent_metadata")
                if isinstance(data.get("user_agent_metadata"), dict)
                else None
            ),
        )
    raise ValueError("cookie file must contain a cookie list or bundle")


def load_cookie_bundle(path: str) -> CookieBundle:
    with open(path, encoding="utf-8") as handle:
        return decode_cookie_bundle(json.load(handle))


def encode_cookie_bundle(
    cookies: list[dict],
    user_agent: str | None,
    user_agent_metadata: dict | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "user_agent": user_agent,
        "user_agent_metadata": user_agent_metadata,
        "cookies": cookies,
    }
