import importlib

import pytest

PACKAGES = [
    "backend.callbacks",
    "backend.db",
    "backend.dispatcher",
    "backend.gateway",
    "backend.receiver",
    "backend.simulator",
    "loadgen",
]


@pytest.mark.parametrize("name", PACKAGES)
def test_package_imports(name):
    importlib.import_module(name)
