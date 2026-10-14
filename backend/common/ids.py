"""ID and time helpers."""

import uuid
from datetime import UTC, datetime


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


def new_batch_id() -> str:
    return f"bat_{uuid.uuid4().hex}"


def utc_now() -> datetime:
    return datetime.now(UTC)
