"""Embedded callback receiver used by the load generator.

Same behaviour as backend/receiver: rejects the first `fail_first_n` attempts per batch with
HTTP 500, then accepts and records the payload with its arrival time.
"""

import time
from collections import defaultdict
from typing import Any

from aiohttp import web


class CallbackReceiver:
    def __init__(self, host: str, port: int, fail_first_n: int = 0) -> None:
        self.host, self.port, self.fail_first_n = host, port, fail_first_n
        self.attempts: dict[str, int] = defaultdict(int)
        self.received: dict[str, dict[str, Any]] = {}
        self._runner: web.AppRunner | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/callback"

    async def _handle(self, request: web.Request) -> web.Response:
        body = await request.json()
        batch_id = str(body.get("batch_id"))
        self.attempts[batch_id] += 1
        if self.attempts[batch_id] <= self.fail_first_n:
            return web.json_response({"accepted": False}, status=500)
        self.received.setdefault(
            batch_id,
            {"received_at": time.time(), "attempt_seen": self.attempts[batch_id], "payload": body},
        )
        return web.json_response({"accepted": True})

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/callback", self._handle)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, self.host, self.port).start()

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
