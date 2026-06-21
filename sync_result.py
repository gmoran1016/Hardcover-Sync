"""Structured destination operation results."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SyncResult:
    success: bool
    reason: str = ""
    target_url: str | None = None
    retryable: bool = True

    @classmethod
    def ok(cls, target_url: str | None = None) -> "SyncResult":
        return cls(True, target_url=target_url, retryable=False)

    @classmethod
    def failed(
        cls,
        reason: str,
        *,
        target_url: str | None = None,
        retryable: bool = True,
    ) -> "SyncResult":
        return cls(False, reason=reason, target_url=target_url, retryable=retryable)
