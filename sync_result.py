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


@dataclass(frozen=True)
class SyncRunResult:
    """Outcome of one complete cycle, including whether it is safe to exit 0."""

    success: bool
    reason: str = ""
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0

    def __bool__(self) -> bool:
        return self.success
