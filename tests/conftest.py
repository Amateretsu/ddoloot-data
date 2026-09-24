"""Root test configuration.

Removes loguru's default stderr handler so log output does not pollute
pytest's captured output. Individual tests that need to assert on log
messages can add their own handler via loguru's `add()` API.
"""

from loguru import logger


def pytest_configure(config):  # noqa: ARG001
    logger.remove()  # drop the default stderr sink
