"""All Redis key names in one place (prefix avoids clashes with other apps on the same Redis)."""

PREFIX = "ig"


def queue(model: str) -> str:
    """Per-model request queue."""
    return f"{PREFIX}:q:{model}"


def rl_requests(model: str, second: int) -> str:
    """Requests dispatched for a model in one epoch-second bucket (sliding 60s window)."""
    return f"{PREFIX}:rl:{model}:req:{second}"


def rl_tokens(model: str, second: int) -> str:
    """Tokens dispatched for a model in one epoch-second bucket."""
    return f"{PREFIX}:rl:{model}:tok:{second}"


def limits(model: str) -> str:
    """Current RPM/TPM limits hash for a model (hot-reloadable)."""
    return f"{PREFIX}:limits:{model}"


def request(request_id: str) -> str:
    """Live state hash for one request."""
    return f"{PREFIX}:req:{request_id}"


def batch(batch_id: str) -> str:
    """Live counters hash for one batch."""
    return f"{PREFIX}:batch:{batch_id}"


def batch_pending(batch_id: str) -> str:
    """Number of not-yet-final requests in a batch; callback fires when it hits 0."""
    return f"{PREFIX}:batch:{batch_id}:pending"


RETRY_ZSET = f"{PREFIX}:retry"  # request ids scored by next-attempt time
CALLBACK_ZSET = f"{PREFIX}:callbacks"  # batch ids scored by next delivery time
