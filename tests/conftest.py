import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session")
def app_client():
    """Run the process-wide ASGI lifespan once, matching production."""

    with TestClient(app) as client:
        yield client
