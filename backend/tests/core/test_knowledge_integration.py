from uuid import uuid4

from fastapi.testclient import TestClient

from ips.core.config import Settings
from ips.main import create_app


def test_main_app_registers_knowledge_routes():
    app = create_app(Settings())
    assert "/api/v1/knowledge/sections" in app.openapi()["paths"]
    with TestClient(app) as client:
        assert client.get("/api/v1/knowledge/sections").status_code == 401


def test_real_knowledge_api_supports_revisions_and_txt_with_tenant_isolation():
    app = create_app(Settings())
    with TestClient(app) as client:
        admin = client.post(
            "/api/v1/auth/register",
            headers={"Origin": "http://localhost:5173"},
            json={
                "organization_name": "Knowledge",
                "email": f"{uuid4()}@example.org",
                "password": "correct-horse-battery",
            },
        )
        assert admin.status_code == 201, admin.text
        headers = {"Origin": "http://localhost:5173", "X-CSRF-Token": admin.json()["csrf_token"]}
        section = client.post(
            "/api/v1/knowledge/sections", headers=headers, json={"name": "Support"}
        )
        assert section.status_code == 201, section.text
        section_id = section.json()["id"]
        article = client.post(
            "/api/v1/knowledge/articles",
            headers=headers,
            json={
                "section_id": section_id,
                "title": "Guide",
                "body": "Original",
                "visibility": "internal",
                "status": "draft",
            },
        )
        assert article.status_code == 201, article.text
        article_id = article.json()["id"]
        path = f"/api/v1/knowledge/articles/{article_id}"
        update = client.patch(
            path, headers=headers, json={"expected_revision": 1, "body": "Updated"}
        )
        assert update.status_code == 200 and update.json()["revision"] == 2
        assert (
            client.patch(
                path, headers=headers, json={"expected_revision": 1, "body": "Stale"}
            ).status_code
            == 409
        )
        restored = client.post(f"{path}/restore/1", headers=headers)
        assert restored.status_code == 200 and restored.json()["revision"] == 3
        assert restored.json()["body"] == "Original"
        imported = client.post(
            "/api/v1/knowledge/imports",
            headers=headers,
            data={"section_id": section_id, "visibility": "public"},
            files={"file": ("guide.txt", b"Text", "text/plain")},
        )
        assert imported.status_code == 202, imported.text
        assert imported.json()["state"] == "ready"
        invalid = client.post(
            "/api/v1/knowledge/imports",
            headers=headers,
            data={"section_id": section_id, "visibility": "public"},
            files={"file": ("bad.txt", b"\xff", "text/plain")},
        )
        assert invalid.status_code == 202 and invalid.json()["error_code"] == "invalid_utf8"
        foreign = client.post(
            "/api/v1/auth/register",
            headers={"Origin": "http://localhost:5173"},
            json={
                "organization_name": "Other",
                "email": f"{uuid4()}@example.org",
                "password": "correct-horse-battery",
            },
        )
        assert foreign.status_code == 201
        assert client.get(path).status_code == 404
