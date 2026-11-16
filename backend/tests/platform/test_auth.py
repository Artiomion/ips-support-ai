from typing import Annotated
from uuid import UUID, uuid4

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import text

from ips.core.config import Settings
from ips.core.ports import TenantContext
from ips.main import create_app
from ips.platform.auth import require_role, require_tenant, tenant_context


@pytest.fixture
def client():
    settings = Settings(web_origin="http://localhost:5173", cookie_secure=False)
    app = create_app(settings)

    admin_guard = require_role("admin")

    @app.get("/test/admin")
    def admin(ctx: Annotated[TenantContext, Depends(admin_guard)]):
        return {"tenant_id": str(ctx.tenant_id)}

    @app.get("/test/tenant/{tenant_id}")
    def tenant(tenant_id: str, ctx: Annotated[TenantContext, Depends(tenant_context)]):
        require_tenant(ctx, UUID(tenant_id))
        return {"ok": True}

    with TestClient(app) as client:
        yield client


def register(client, email=None):
    email = email or f"{uuid4()}@example.org"
    response = client.post(
        "/api/v1/auth/register",
        headers={"Origin": "http://localhost:5173"},
        json={"organization_name": "Company", "email": email, "password": "correct-horse-battery"},
    )
    assert response.status_code == 201, response.text
    return response.json(), email


def test_register_hashes_password_and_session(client):
    data, email = register(client)
    assert data["scope"] == "tenant" and data["role"] == "admin"
    raw = client.cookies.get("ips_session")
    assert raw
    with client.app.state.engine.connect() as conn:
        row = conn.execute(
            text("SELECT password_hash FROM users WHERE normalized_email = :email"),
            {"email": email},
        ).one()
        assert row.password_hash.startswith("$argon2")
        session = conn.execute(
            text("SELECT token_hash FROM staff_sessions WHERE user_id = :id"),
            {"id": data["user_id"]},
        ).one()
        assert session.token_hash != raw
    assert client.get("/api/v1/auth/me").json()["tenant_id"] == data["tenant_id"]


def test_origin_and_csrf_required(client):
    body = {
        "organization_name": "Company",
        "email": f"{uuid4()}@example.org",
        "password": "correct-horse-battery",
    }
    assert client.post("/api/v1/auth/register", json=body).status_code in (403, 422)
    assert (
        client.post(
            "/api/v1/auth/register", headers={"Origin": "https://attacker.example"}, json=body
        ).status_code
        == 403
    )
    session, _ = register(client)
    assert client.post(
        "/api/v1/auth/logout", headers={"Origin": "http://localhost:5173"}
    ).status_code in (403, 422)
    assert (
        client.post(
            "/api/v1/auth/logout",
            headers={"Origin": "https://attacker.example", "X-CSRF-Token": session["csrf_token"]},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/auth/logout",
            headers={"Origin": "http://localhost:5173", "X-CSRF-Token": session["csrf_token"]},
        ).status_code
        == 204
    )
    assert client.get("/api/v1/auth/me").status_code == 401


def test_tenant_isolation_and_role_checks(client):
    first, _ = register(client)
    assert client.get("/test/admin").status_code == 200
    assert client.get(f"/test/tenant/{uuid4()}").status_code == 404
    with client.app.state.engine.begin() as conn:
        conn.execute(
            text("UPDATE memberships SET role='specialist' WHERE id = :id"),
            {"id": first["membership_id"]},
        )
    assert client.get("/test/admin").status_code == 403
    second, _ = register(client)
    assert first["tenant_id"] != second["tenant_id"]
    assert client.get(f"/test/tenant/{first['tenant_id']}").status_code == 404


def test_login_rejects_bad_credentials_and_requires_origin(client):
    _, email = register(client)
    assert client.post(
        "/api/v1/auth/login", json={"email": email, "password": "correct-horse-battery"}
    ).status_code in (403, 422)
    assert (
        client.post(
            "/api/v1/auth/login",
            headers={"Origin": "http://localhost:5173"},
            json={"email": email, "password": "wrong"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login",
            headers={"Origin": "http://localhost:5173"},
            json={"email": email, "password": "correct-horse-battery"},
        ).status_code
        == 200
    )


def test_superadmin_has_platform_scope_and_no_tenant_access(client):
    session, email = register(client)
    with client.app.state.engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET is_superadmin = true WHERE id = :id"), {"id": session["user_id"]}
        )
        conn.execute(
            text("DELETE FROM memberships WHERE id = :id"), {"id": session["membership_id"]}
        )
    logged_in = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "http://localhost:5173"},
        json={"email": email, "password": "correct-horse-battery"},
    )
    assert logged_in.status_code == 200
    assert logged_in.json()["scope"] == "platform"
    assert client.get("/test/admin").status_code == 403
    assert client.get(f"/test/tenant/{session['tenant_id']}").status_code == 403


def test_removed_membership_immediately_revokes_access(client):
    session, _ = register(client)
    with client.app.state.engine.begin() as conn:
        conn.execute(
            text("DELETE FROM memberships WHERE id = :id"), {"id": session["membership_id"]}
        )
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/test/admin").status_code == 401


def test_logout_revokes_even_a_copied_token(client):
    session, _ = register(client)
    stolen = client.cookies.get("ips_session")
    assert (
        client.post(
            "/api/v1/auth/logout",
            headers={"Origin": "http://localhost:5173", "X-CSRF-Token": session["csrf_token"]},
        ).status_code
        == 204
    )
    client.cookies.set("ips_session", stolen)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_rejects_foreign_tenant_on_login(client):
    _, email = register(client)
    result = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "http://localhost:5173"},
        json={"email": email, "password": "correct-horse-battery", "organization_id": str(uuid4())},
    )
    assert result.status_code == 404


def test_frontend_cors_is_limited_to_staff_origin(client):
    preflight = {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-csrf-token",
    }
    allowed = client.options(
        "/api/v1/auth/logout", headers={**preflight, "Origin": "http://localhost:5173"}
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert allowed.headers["access-control-allow-credentials"] == "true"
    denied = client.options(
        "/api/v1/auth/logout", headers={**preflight, "Origin": "https://attacker.example"}
    )
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


def test_registration_rejects_blank_organization(client):
    body = {
        "organization_name": "   ",
        "email": f"{uuid4()}@example.org",
        "password": "correct-horse-battery",
    }
    assert (
        client.post(
            "/api/v1/auth/register", headers={"Origin": "http://localhost:5173"}, json=body
        ).status_code
        == 422
    )


def test_production_rejects_default_session_secret_and_insecure_cookies():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(app_env="production", cookie_secure=True)
    with pytest.raises(ValidationError):
        Settings(app_env="production", cookie_secure=False, session_secret="s" * 48)


def test_no_organization_switch_without_membership(client):
    _, email = register(client)
    other, _ = register(client)
    response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "http://localhost:5173"},
        json={
            "email": email,
            "password": "correct-horse-battery",
            "organization_id": other["tenant_id"],
        },
    )
    assert response.status_code == 404
