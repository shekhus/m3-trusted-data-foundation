"""Adapter protocol: discover what a source offers, fetch one extract's bytes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class SourceUnavailable(RuntimeError):
    """The source exists but cannot be reached right now; the caller should retry later."""

    def __init__(self, message: str, retry_after_seconds: int = 300) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class ExtractRef:
    source: str
    name: str
    produced_at: datetime  # when the source system wrote the extract (UTC); drives the stale flag


@dataclass(frozen=True)
class Extract:
    ref: ExtractRef
    content: bytes


class SourceAdapter(Protocol):
    def sources(self) -> list[str]: ...

    def discover(self, source: str) -> list[ExtractRef]:
        """Extracts the source currently offers, oldest name first. Raises SourceUnavailable."""
        ...

    def fetch(self, ref: ExtractRef) -> Extract:
        """The extract's bytes. Raises SourceUnavailable."""
        ...
