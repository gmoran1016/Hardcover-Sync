"""Validated runtime configuration."""

from dataclasses import dataclass
import os

from dotenv import load_dotenv


class ConfigError(ValueError):
    """Raised when runtime configuration is invalid."""


@dataclass(frozen=True)
class Config:
    hardcover_api_key: str
    goodreads_email: str
    goodreads_password: str
    storygraph_email: str
    storygraph_password: str
    sync_interval_seconds: int
    state_file: str
    goodreads_enabled: bool = True
    storygraph_enabled: bool = True


def _env_bool(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    if value.strip().casefold() in {"1", "true", "yes", "on"}:
        return True
    if value.strip().casefold() in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false")


def load_config(*, require_api_key: bool = True) -> Config:
    load_dotenv()

    api_key = os.getenv("HARDCOVER_API_KEY", "").strip()
    if require_api_key and (not api_key or api_key == "your_hardcover_api_key_here"):
        raise ConfigError("HARDCOVER_API_KEY must be configured")

    raw_interval = os.getenv("SYNC_INTERVAL_MINUTES", "30").strip()
    try:
        interval_minutes = int(raw_interval)
    except ValueError as exc:
        raise ConfigError("SYNC_INTERVAL_MINUTES must be an integer") from exc
    if not 1 <= interval_minutes <= 1440:
        raise ConfigError("SYNC_INTERVAL_MINUTES must be between 1 and 1440")

    base_dir = os.path.dirname(__file__)
    state_file = os.getenv(
        "STATE_FILE",
        os.path.join(base_dir, "state", "sync_state.json"),
    )

    return Config(
        hardcover_api_key=api_key,
        goodreads_email=os.getenv("GOODREADS_EMAIL", "").strip(),
        goodreads_password=os.getenv("GOODREADS_PASSWORD", ""),
        storygraph_email=os.getenv("STORYGRAPH_EMAIL", "").strip(),
        storygraph_password=os.getenv("STORYGRAPH_PASSWORD", ""),
        sync_interval_seconds=interval_minutes * 60,
        state_file=state_file,
        goodreads_enabled=_env_bool("GOODREADS_ENABLED"),
        storygraph_enabled=_env_bool("STORYGRAPH_ENABLED"),
    )
