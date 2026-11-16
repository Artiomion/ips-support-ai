from fastapi.testclient import TestClient

from ips.core.config import Settings
from ips.main import create_app


def test_liveness():
    with TestClient(create_app()) as client:
        assert client.get("/health/live").status_code == 200


def test_readiness_without_database():
    settings = Settings(database_url="postgresql+psycopg://ips:ips@127.0.0.1:1/ips")
    with TestClient(create_app(settings)) as client:
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert "traceback" not in response.text.lower()
