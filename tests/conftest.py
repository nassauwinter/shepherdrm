"""Configure shared pytest behavior and test-environment defaults."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
