"""Describe the machine a run happened on (for the benchmark report)."""

import os
import platform
import subprocess
from importlib import metadata


def _cpu_name() -> str:
    if platform.system() == "Darwin":
        try:
            return subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True
            ).stdout.strip()
        except OSError:
            pass
    return platform.processor() or platform.machine()


def _ram_gb() -> float | None:
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3, 1)
    except (ValueError, OSError, AttributeError):
        return None


def machine_info() -> dict:
    versions = {}
    for pkg in ("fastapi", "uvicorn", "aiohttp", "redis", "sqlalchemy", "asyncpg", "pydantic"):
        try:
            versions[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            versions[pkg] = None
    return {
        "os": platform.platform(),
        "cpu": _cpu_name(),
        "cpu_count": os.cpu_count(),
        "ram_gb": _ram_gb(),
        "python": platform.python_version(),
        "packages": versions,
    }
