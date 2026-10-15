"""Exact rolling-window counter used by the simulator to police limits.

Deliberately a *different* algorithm from the dispatcher's bucket limiter: it keeps every
event timestamp, so it measures any real 60-second period (t-60, t] exactly. If the limiter
had a bug, this independent referee would catch it.
"""

from collections import deque


class ExactWindow:
    def __init__(self, window_seconds: float = 60.0) -> None:
        self.window = window_seconds
        self._events: deque[tuple[float, int]] = deque()
        self.requests = 0
        self.tokens = 0
        self.max_requests = 0  # most requests ever seen in one window
        self.max_tokens = 0  # most tokens ever seen in one window

    def _prune(self, t: float) -> None:
        cutoff = t - self.window
        while self._events and self._events[0][0] <= cutoff:
            _, tok = self._events.popleft()
            self.requests -= 1
            self.tokens -= tok

    def current(self, t: float) -> tuple[int, int]:
        self._prune(t)
        return self.requests, self.tokens

    def would_exceed(self, t: float, tokens: int, rpm: int, tpm: int) -> bool:
        self._prune(t)
        return self.requests + 1 > rpm or self.tokens + tokens > tpm

    def retry_after(self, t: float) -> float:
        """Seconds until the oldest event leaves the window."""
        self._prune(t)
        if not self._events:
            return 0.0
        return max(0.0, self._events[0][0] + self.window - t)

    def add(self, t: float, tokens: int) -> None:
        self._prune(t)
        self._events.append((t, tokens))
        self.requests += 1
        self.tokens += tokens
        self.max_requests = max(self.max_requests, self.requests)
        self.max_tokens = max(self.max_tokens, self.tokens)


class BucketWindow:
    """Approximate rolling window in fixed buckets (default 100 ms).

    Used by the high-throughput benchmark: at 300k+ requests/second, keeping every timestamp
    (ExactWindow) would hold ~18M entries per minute. Here the window is the last 600 buckets,
    so its edge is accurate to one bucket (0.1 s). Same interface as ExactWindow.
    """

    def __init__(self, window_seconds: float = 60.0, resolution: float = 0.1) -> None:
        self.res = resolution
        self.n = round(window_seconds / resolution)
        self._req = [0] * self.n
        self._tok = [0] * self.n
        self._head: int | None = None
        self.requests = 0
        self.tokens = 0
        self.max_requests = 0
        self.max_tokens = 0

    def _advance(self, t: float) -> int:
        b = int(t / self.res)
        if self._head is None:
            self._head = b
            return b
        if b <= self._head:
            return self._head
        for i in range(1, min(b - self._head, self.n) + 1):
            idx = (self._head + i) % self.n
            self.requests -= self._req[idx]
            self.tokens -= self._tok[idx]
            self._req[idx] = 0
            self._tok[idx] = 0
        self._head = b
        return b

    def current(self, t: float) -> tuple[int, int]:
        self._advance(t)
        return self.requests, self.tokens

    def would_exceed(self, t: float, tokens: int, rpm: int, tpm: int) -> bool:
        self._advance(t)
        return self.requests + 1 > rpm or self.tokens + tokens > tpm

    def retry_after(self, t: float) -> float:
        head = self._advance(t)
        for b in range(head - self.n + 1, head + 1):
            if self._req[b % self.n]:
                return max(0.0, (b + self.n) * self.res - t)
        return 0.0

    def add(self, t: float, tokens: int) -> None:
        idx = self._advance(t) % self.n
        self._req[idx] += 1
        self._tok[idx] += tokens
        self.requests += 1
        self.tokens += tokens
        self.max_requests = max(self.max_requests, self.requests)
        self.max_tokens = max(self.max_tokens, self.tokens)
