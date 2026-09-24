"""Root test configuration.

Removes loguru's default stderr handler so log output does not pollute
pytest's captured output. Individual tests that need to assert on log
messages can add their own handler via loguru's `add()` API.

Blocks network access: ADR 0006 forbids unattended traffic to the wiki, so no test may
resolve or connect to a non-local host. A test that tries gets an ``OSError``.
"""

import socket

import pytest
from loguru import logger

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", None}


def pytest_configure(config):  # noqa: ARG001
    logger.remove()  # drop the default stderr sink


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    real_getaddrinfo = socket.getaddrinfo

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host not in _LOCAL_HOSTS:
            raise OSError(f"network access is blocked in tests: {host}")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
