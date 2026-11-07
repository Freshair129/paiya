"""Synthetic feed collection and run execution."""

from paiya.collector.adapter import CollectorAdapter, FeedCard, FeedRunData
from paiya.collector.feed_script import FeedScriptAdapter
from paiya.collector.run import RunResult, execute_discovery

__all__ = [
    "CollectorAdapter",
    "FeedCard",
    "FeedRunData",
    "FeedScriptAdapter",
    "RunResult",
    "execute_discovery",
]
