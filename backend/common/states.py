"""Request / batch states and the request state machine."""

from enum import StrEnum


class RequestState(StrEnum):
    RECEIVED = "received"  # accepted and recorded, not yet queued
    QUEUED = "queued"  # waiting in its model's queue
    DISPATCHED = "dispatched"  # capacity reserved, provider call in flight
    RETRY_WAIT = "retry_wait"  # transient failure, waiting for backoff
    SUCCEEDED = "succeeded"  # final
    FAILED = "failed"  # final: permanent error or retries exhausted
    EXPIRED = "expired"  # final: waited longer than max_wait
    REJECTED = "rejected"  # final: invalid input or queue full


S = RequestState

ALLOWED_TRANSITIONS: dict[RequestState, frozenset[RequestState]] = {
    S.RECEIVED: frozenset({S.QUEUED, S.REJECTED}),
    S.QUEUED: frozenset({S.DISPATCHED, S.EXPIRED}),
    S.DISPATCHED: frozenset({S.SUCCEEDED, S.FAILED, S.RETRY_WAIT}),
    S.RETRY_WAIT: frozenset({S.QUEUED, S.EXPIRED}),
    S.SUCCEEDED: frozenset(),
    S.FAILED: frozenset(),
    S.EXPIRED: frozenset(),
    S.REJECTED: frozenset(),
}

TERMINAL_STATES = frozenset({S.SUCCEEDED, S.FAILED, S.EXPIRED, S.REJECTED})
COMPLETED_STATES = frozenset({S.SUCCEEDED, S.FAILED})  # provider reached a final outcome
WAITING_STATES = frozenset({S.RECEIVED, S.QUEUED, S.DISPATCHED, S.RETRY_WAIT})


class InvalidTransitionError(ValueError):
    pass


def can_transition(current: RequestState, new: RequestState) -> bool:
    return new in ALLOWED_TRANSITIONS[current]


def ensure_transition(current: RequestState, new: RequestState) -> None:
    if not can_transition(current, new):
        raise InvalidTransitionError(f"illegal transition {current} -> {new}")


def is_terminal(state: RequestState) -> bool:
    return state in TERMINAL_STATES


def accounting_bucket(state: RequestState) -> str:
    """Map a state to one of the 4 buckets used in reports:
    submitted = completed + waiting + expired + rejected."""
    if state in COMPLETED_STATES:
        return "completed"
    if state in WAITING_STATES:
        return "waiting"
    if state is S.EXPIRED:
        return "expired"
    return "rejected"


class FailureKind(StrEnum):
    TRANSIENT = "transient"  # 429 / 5xx / timeout -> retry
    PERMANENT = "permanent"  # 4xx (not 429) -> fail immediately


class BatchStatus(StrEnum):
    PROCESSING = "processing"
    COMPLETED = "completed"  # all final, no failures
    COMPLETED_WITH_FAILURES = "completed_with_failures"  # all final, some failed/expired/rejected


class CallbackStatus(StrEnum):
    NOT_REQUESTED = "not_requested"  # batch had no callback_url
    PENDING = "pending"  # batch not finished yet
    DELIVERING = "delivering"  # attempts in progress
    DELIVERED = "delivered"
    DEAD_LETTERED = "dead_lettered"  # gave up after max attempts
