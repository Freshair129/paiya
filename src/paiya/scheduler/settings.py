"""Tunables for the A3 scheduler and collector, with contract defaults."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SchedulerSettings:
    lease_minutes: int = 5
    max_retries: int = 3
    backoff_minutes: tuple[int, ...] = (1, 2, 4)
    time_budget_seconds: int = 120
    scroll_budget_steps: int = 30
    overlap_count: int = 3
    cards_per_step: int = 5

    def __post_init__(self) -> None:
        if self.lease_minutes <= 0:
            raise ValueError("lease_minutes must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if any(minutes <= 0 for minutes in self.backoff_minutes):
            raise ValueError("backoff_minutes values must be positive")
        if not self.backoff_minutes and self.max_retries:
            raise ValueError("backoff_minutes cannot be empty when retries are enabled")
        if self.time_budget_seconds <= 0 or self.scroll_budget_steps <= 0:
            raise ValueError("time and scroll budgets must be positive")
        if self.overlap_count <= 0 or self.cards_per_step != 5:
            raise ValueError("overlap_count must be positive and cards_per_step is fixed at 5")
