import pytest

from item_extractor import load_config


@pytest.fixture(scope="session")
def cfg():
    return load_config()
