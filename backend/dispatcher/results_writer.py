"""Writes final request results to Postgres in batches.

Single-row inserts at hundreds of requests/sec would be the bottleneck; buffering and
writing up to `max_rows` rows per INSERT keeps Postgres off the hot path.
ON CONFLICT DO NOTHING makes re-writes harmless (request_id is the primary key).
"""

import asyncio
import contextlib
import logging
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.tables import RequestRow

log = logging.getLogger(__name__)


class ResultsWriter:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        flush_interval: float = 0.25,
        max_rows: int = 2_000,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._interval = flush_interval
        self._max_rows = max_rows
        self._buffer: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.rows_written = 0

    def add(self, row: dict[str, Any]) -> None:
        self._buffer.append(row)
        if len(self._buffer) >= self._max_rows:
            self._wake.set()

    async def flush(self) -> int:
        """Write everything buffered so far. Safe to call from several tasks at once."""
        async with self._lock:
            written = 0
            while self._buffer:
                chunk = self._buffer[: self._max_rows]
                del self._buffer[: self._max_rows]
                stmt = (
                    insert(RequestRow)
                    .values(chunk)
                    .on_conflict_do_nothing(index_elements=["request_id"])
                )
                try:
                    async with self._sessionmaker() as session, session.begin():
                        await session.execute(stmt)
                except Exception:
                    self._buffer[:0] = chunk  # keep the rows; the next flush retries them
                    raise
                written += len(chunk)
            self.rows_written += written
            return written

    async def _run(self) -> None:
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval)
            self._wake.clear()
            try:
                await self.flush()
            except Exception:  # rows stay buffered and are retried on the next flush
                log.exception("failed to write results to Postgres")

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="results-writer")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self.flush()
