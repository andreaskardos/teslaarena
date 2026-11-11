"""Small in-memory rate limits for HTTP requests and WebSocket actions."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

Clock = Callable[[], float]


@dataclass
class WindowLimiter:
    """Allow at most ``limit`` events per key within a sliding time window."""

    limit: int
    window_seconds: float
    clock: Clock = time.monotonic
    _events: dict[str, deque[float]] = field(default_factory=dict)

    def allow(self, key: str) -> bool:
        now = self.clock()
        events = self._events.setdefault(key, deque())
        while events and now - events[0] >= self.window_seconds:
            events.popleft()
        if len(events) >= self.limit:
            return False
        events.append(now)
        if len(self._events) > 1000:
            self._forget_idle_keys(now)
        return True

    def reset(self) -> None:
        self._events.clear()

    def _forget_idle_keys(self, now: float) -> None:
        for key, events in list(self._events.items()):
            if not events or now - events[-1] >= self.window_seconds:
                del self._events[key]


@dataclass
class TokenBucket:
    """Allow bursts of ``capacity`` events, refilled at ``rate`` per second."""

    rate: float
    capacity: float
    clock: Clock = time.monotonic
    tokens: float = field(init=False)
    updated_at: float = field(init=False)

    def __post_init__(self) -> None:
        self.tokens = self.capacity
        self.updated_at = self.clock()

    def allow(self) -> bool:
        now = self.clock()
        self.tokens = min(
            self.capacity, self.tokens + (now - self.updated_at) * self.rate
        )
        self.updated_at = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True
