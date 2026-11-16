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


def test_validation_errors_follow_safe_contract():
    from pydantic import BaseModel

    class Input(BaseModel):
        name: str

    app = create_app()

    @app.post("/test-validation")
    def handler(body: Input):
        return body

    with TestClient(app) as client:
        response = client.post("/test-validation", json={"name": 42})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["request_id"]
    assert "traceback" not in response.text.lower()


def test_unexpected_error_does_not_disclose_exception():
    app = create_app()

    @app.get("/test-internal")
    def handler():
        raise RuntimeError("private database password")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/test-internal")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "private database password" not in response.text
