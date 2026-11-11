import pytest

from tesla_arena import server


@pytest.fixture(autouse=True)
def reset_rate_limits() -> None:
    """Every test client shares one address, so start each test with no history."""
    server.room_creation_limiter.reset()
    server.join_limiter.reset()
