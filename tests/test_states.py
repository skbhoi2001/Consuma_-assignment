import pytest

from backend.common.states import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    InvalidTransitionError,
    accounting_bucket,
    can_transition,
    ensure_transition,
)
from backend.common.states import (
    RequestState as S,
)


def test_every_state_has_transition_entry():
    assert set(ALLOWED_TRANSITIONS) == set(S)


def test_terminal_states_have_no_exits():
    for state in TERMINAL_STATES:
        assert ALLOWED_TRANSITIONS[state] == frozenset()


@pytest.mark.parametrize(
    "path",
    [
        [S.RECEIVED, S.QUEUED, S.DISPATCHED, S.SUCCEEDED],
        [S.RECEIVED, S.QUEUED, S.DISPATCHED, S.RETRY_WAIT, S.QUEUED, S.DISPATCHED, S.FAILED],
        [S.RECEIVED, S.QUEUED, S.EXPIRED],
        [S.RECEIVED, S.REJECTED],
    ],
)
def test_valid_paths(path):
    for a, b in zip(path, path[1:], strict=False):
        ensure_transition(a, b)


@pytest.mark.parametrize(
    "a,b",
    [
        (S.SUCCEEDED, S.QUEUED),
        (S.RECEIVED, S.DISPATCHED),  # must queue first
        (S.RETRY_WAIT, S.DISPATCHED),  # retry must go back through the limiter
        (S.FAILED, S.RETRY_WAIT),
    ],
)
def test_invalid_transitions(a, b):
    assert not can_transition(a, b)
    with pytest.raises(InvalidTransitionError):
        ensure_transition(a, b)


def test_every_state_maps_to_exactly_one_bucket():
    buckets = {accounting_bucket(s) for s in S}
    assert buckets == {"completed", "waiting", "expired", "rejected"}
    assert accounting_bucket(S.SUCCEEDED) == "completed"
    assert accounting_bucket(S.RETRY_WAIT) == "waiting"
