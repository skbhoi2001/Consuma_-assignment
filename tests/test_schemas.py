import pytest
from pydantic import ValidationError

from backend.common.schemas import (
    AccountingSnapshot,
    BatchCreate,
    InferenceRequest,
    LimitUpdate,
)


def req(**kw):
    base = {"model": "model-a", "estimated_tokens": 1000, "payload": {"prompt": "hi"}}
    base.update(kw)
    return base


def test_valid_request():
    r = InferenceRequest(**req())
    assert r.model == "model-a"
    assert r.request_id is None


@pytest.mark.parametrize("tokens", [0, -5, 1_000_001])
def test_bad_token_counts_rejected(tokens):
    with pytest.raises(ValidationError):
        InferenceRequest(**req(estimated_tokens=tokens))


def test_bad_model_name_rejected():
    with pytest.raises(ValidationError):
        InferenceRequest(**req(model="bad name!"))


def test_unknown_field_rejected():
    with pytest.raises(ValidationError):
        InferenceRequest(**req(extra_field=1))


def test_batch_requires_at_least_one_request():
    with pytest.raises(ValidationError):
        BatchCreate(requests=[])


def test_batch_rejects_duplicate_client_ids():
    with pytest.raises(ValidationError):
        BatchCreate(requests=[req(request_id="x"), req(request_id="x")])


def test_batch_with_callback():
    b = BatchCreate(requests=[req(), req()], callback_url="http://localhost:9100/callbacks/receive")
    assert len(b.requests) == 2
    assert str(b.callback_url).startswith("http://localhost:9100")


def test_limit_update_needs_a_field():
    with pytest.raises(ValidationError):
        LimitUpdate()
    assert LimitUpdate(rpm=5000).rpm == 5000


def test_accounting_snapshot_balance():
    snap = AccountingSnapshot(
        submitted=100, succeeded=70, failed=5, waiting=15, expired=6, rejected=4
    )
    assert snap.completed == 75
    assert snap.balanced
    snap.waiting = 14
    assert not snap.balanced
