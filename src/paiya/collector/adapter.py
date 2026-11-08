"""Adapter boundary between the scheduler and a synthetic or future collector."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

ADAPTER_VERSION = "fixture-v1"


@dataclass(frozen=True)
class FeedCard:
    source_post_key: str | None
    pinned: bool
    suggested: bool
    parse: str
    envelope: dict[str, Any] | None


@dataclass(frozen=True)
class FeedRunData:
    label: str
    started_at: datetime
    session: str
    sort: str
    access: str
    batches: tuple[tuple[FeedCard, ...], ...]


class CollectorAdapter(Protocol):
    """Read one bounded feed run without exposing browser or network details."""

    def read_run(self, group_key: str, run_label: str, cards_per_step: int) -> FeedRunData:
        """Return the run metadata and cards in load-order batches."""
