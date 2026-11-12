import pytest

from tesla_arena import rooms, server


@pytest.fixture(autouse=True)
def reset_rate_limits() -> None:
    """Every test client shares one address, so start each test with no history."""
    server.room_creation_limiter.reset()
    server.join_limiter.reset()


@pytest.fixture(autouse=True)
def no_countdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start matches immediately; countdown tests set their own length."""
    monkeypatch.setattr(rooms, "COUNTDOWN_SECONDS", 0.0)
