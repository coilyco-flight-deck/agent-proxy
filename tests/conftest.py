import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session")
def app_client():
    """Run the process-wide ASGI lifespan once, matching production."""

    with TestClient(app) as client:
        yield client


@pytest.fixture(autouse=True)
def _rate_limit_off(monkeypatch):
    """Shedding is on by default in production; the suite pins it off.

    Every other test fires requests far above 1/s, so leaving the default live
    would turn this into a rate-limit suite. tests/test_ratelimit.py turns it
    back on and is the one place the behaviour is exercised.
    """
    from app.config import get_settings
    from app.ratelimit import get_rate_limiter

    monkeypatch.setattr(get_settings(), "rate_limit_per_second", 0.0)
    get_rate_limiter().reset()
    yield
    get_rate_limiter().reset()
