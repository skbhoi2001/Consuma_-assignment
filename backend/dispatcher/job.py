"""In-memory representation of one request while the dispatcher works on it."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from backend.common.states import RequestState, ensure_transition


def to_dt(ts: float | None) -> datetime | None:
    return None if ts is None else datetime.fromtimestamp(ts, UTC)


@dataclass(slots=True, eq=False)
class Job:
    request_id: str
    model: str
    estimated_tokens: int
    submitted_at: float  # epoch seconds
    deadline: float  # epoch seconds; still waiting after this -> EXPIRED
    payload: dict[str, Any] = field(default_factory=dict)
    client_request_id: str | None = None
    batch_id: str | None = None
    state: RequestState = RequestState.RECEIVED
    attempts: int = 0
    actual_tokens: int | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    finished_at: float | None = None
    call_ms: float | None = None  # duration of the last provider call (excludes queueing)

    def move(self, new: RequestState) -> None:
        """Change state; raises InvalidTransitionError on an illegal move."""
        ensure_transition(self.state, new)
        self.state = new

    @property
    def latency_ms(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.submitted_at) * 1000

    def to_row(self) -> dict[str, Any]:
        """Row for the Postgres `requests` table (final results)."""
        return {
            "request_id": self.request_id,
            "client_request_id": self.client_request_id,
            "batch_id": self.batch_id,
            "model": self.model,
            "state": self.state.value,
            "attempts": self.attempts,
            "estimated_tokens": self.estimated_tokens,
            "actual_tokens": self.actual_tokens,
            "result": self.result,
            "error": self.error,
            "submitted_at": to_dt(self.submitted_at),
            "finished_at": to_dt(self.finished_at),
            "latency_ms": self.latency_ms,
        }
