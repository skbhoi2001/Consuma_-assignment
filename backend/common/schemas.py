"""Pydantic schemas shared by the API, workers, simulator and load generator."""

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, model_validator

from backend.common.states import BatchStatus, CallbackStatus, RequestState

ModelName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]
RequestId = Annotated[str, StringConstraints(min_length=1, max_length=128)]

MAX_ESTIMATED_TOKENS = 1_000_000
MAX_BATCH_SIZE = 100_000


# ---------- Models & limits ----------


class ModelLimits(BaseModel):
    rpm: int = Field(gt=0, description="Requests per minute")
    tpm: int = Field(gt=0, description="Tokens per minute")


class ModelConfig(ModelLimits):
    name: ModelName
    max_queue: int = Field(200_000, gt=0)
    max_wait_seconds: float = Field(120, gt=0)


class LimitUpdate(BaseModel):
    """Body of PATCH /v1/models/{model}/limits. At least one field required."""

    model_config = ConfigDict(extra="forbid")

    rpm: int | None = Field(None, gt=0)
    tpm: int | None = Field(None, gt=0)

    @model_validator(mode="after")
    def at_least_one(self) -> "LimitUpdate":
        if self.rpm is None and self.tpm is None:
            raise ValueError("provide rpm and/or tpm")
        return self


# ---------- Inputs ----------


class InferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: RequestId | None = None  # optional client-supplied id
    model: ModelName
    payload: dict[str, Any] = Field(default_factory=dict)
    estimated_tokens: int = Field(gt=0, le=MAX_ESTIMATED_TOKENS)


class BatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests: list[InferenceRequest] = Field(min_length=1, max_length=MAX_BATCH_SIZE)
    callback_url: HttpUrl | None = None

    @model_validator(mode="after")
    def unique_client_ids(self) -> "BatchCreate":
        ids = [r.request_id for r in self.requests if r.request_id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate request_id in batch")
        return self


# ---------- Outputs ----------


class RequestAck(BaseModel):
    request_id: str
    client_request_id: str | None = None
    state: RequestState
    accepted_at: datetime
    reason: str | None = None  # why it was rejected, if it was


class BatchAck(BaseModel):
    batch_id: str
    status: BatchStatus
    total: int
    accepted_at: datetime
    status_url: str


class RequestStatus(BaseModel):
    request_id: str
    client_request_id: str | None = None
    batch_id: str | None = None
    model: str
    state: RequestState
    attempts: int = 0
    estimated_tokens: int
    actual_tokens: int | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    submitted_at: datetime
    finished_at: datetime | None = None
    latency_ms: float | None = None


class BatchSummary(BaseModel):
    batch_id: str
    status: BatchStatus
    total: int
    succeeded: int = 0
    failed: int = 0
    expired: int = 0
    rejected: int = 0
    pending: int
    created_at: datetime
    completed_at: datetime | None = None
    callback_status: CallbackStatus
    results_url: str


class BatchResultsPage(BaseModel):
    batch_id: str
    items: list[RequestStatus]
    next_cursor: str | None = None  # pass as ?cursor= to get the next page


class CallbackPayload(BaseModel):
    """What we POST to the client's callback_url once every request is final."""

    batch_id: str
    status: BatchStatus
    total: int
    succeeded: int
    failed: int
    expired: int
    rejected: int
    results_url: str
    completed_at: datetime
    delivery_attempt: int


class AccountingSnapshot(BaseModel):
    """submitted = completed + waiting + expired + rejected (Scenario 1 rule)."""

    submitted: int = 0
    succeeded: int = 0
    failed: int = 0
    waiting: int = 0
    expired: int = 0
    rejected: int = 0

    @property
    def completed(self) -> int:
        return self.succeeded + self.failed

    @property
    def balanced(self) -> bool:
        return self.submitted == self.completed + self.waiting + self.expired + self.rejected
