"""Start simulator (:9000), callback receiver (:9100) and gateway (:8000) together.

    python scripts/stack.py          # Ctrl-C stops all three
Logs go to logs/<service>.log. Postgres and Redis must already be running.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

SERVICES = [
    ("simulator", "backend.simulator.app:create_app", 9000),
    ("receiver", "backend.receiver.app:create_app", 9100),
    ("gateway", "backend.gateway.app:create_app", 8000),
]


def _stop(signum, frame):  # SIGTERM (e.g. `kill`) behaves like Ctrl-C
    raise KeyboardInterrupt


def main() -> None:
    signal.signal(signal.SIGTERM, _stop)
    Path("logs").mkdir(exist_ok=True)
    procs = []
    for name, target, port in SERVICES:
        port = int(os.environ.get(f"{name.upper()}_PORT", port))
        log = open(f"logs/{name}.log", "w")  # noqa: SIM115 - closed with the process
        cmd = [
            sys.executable,
            "-m",
            "uvicorn",
            target,
            "--factory",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ]
        procs.append((name, port, subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)))
        for _ in range(100):  # wait for health before starting the next one
            try:
                if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            print(f"{name} did not start - see logs/{name}.log")
        print(f"{name:10s} http://localhost:{port}")
    print("running - Ctrl-C to stop")
    try:
        signal.pause()
    except KeyboardInterrupt:
        pass
    finally:
        for _, _, p in reversed(procs):
            p.terminate()
        for _, _, p in procs:
            p.wait(timeout=10)


if __name__ == "__main__":
    main()
