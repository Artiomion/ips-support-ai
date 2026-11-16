import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from ips.platform.identity import IdentityError, decode_identity, derive_widget_secret


def signed(secret, widget_key, **changes):
    now = int(datetime.now(UTC).timestamp())
    payload = {
        "widget_key": widget_key,
        "external_id": "user-123",
        "nonce": "random-nonce-123456",
        "issued_at": now,
        "expires_at": now + 299,
    }
    payload.update(changes)
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(
        b"="
    )
    signature = base64.urlsafe_b64encode(hmac.new(secret, encoded, hashlib.sha256).digest()).rstrip(
        b"="
    )
    return (encoded + b"." + signature).decode()


def test_identity_signature_and_widget_binding():
    key = "public-widget-key"
    secret = derive_widget_secret("s" * 48, uuid4())
    assert decode_identity(signed(secret, key), secret, key)["external_id"] == "user-123"
    with pytest.raises(IdentityError):
        decode_identity(signed(secret, key, external_id="victim") + "x", secret, key)
    with pytest.raises(IdentityError):
        decode_identity(signed(secret, key), secret, "different-widget")


def test_rejects_long_lived_future_and_malformed_identity():
    key = "public-widget-key"
    secret = derive_widget_secret("s" * 48, uuid4())
    now = int(datetime.now(UTC).timestamp())
    for token in [
        signed(secret, key, issued_at=now, expires_at=now + 301),
        signed(secret, key, issued_at=now + 60, expires_at=now + 120),
        signed(secret, key, issued_at=now - 601, expires_at=now - 300),
        "not.a.token",
        "not-a-token",
    ]:
        with pytest.raises(IdentityError):
            decode_identity(token, secret, key)


def test_widget_origin_requires_exact_origin():
    from ips.platform.widgets import normalize_origin

    assert normalize_origin("https://example.org:443") == "https://example.org"
    assert normalize_origin("http://example.org:80") == "http://example.org"
    assert normalize_origin("https://example.org:8443") == "https://example.org:8443"
    for invalid in [
        "null",
        "https://example.org.attacker.test/path",
        "https://example.org/path",
        "https://example.org?x=y",
        "https://evil@example.org",
        "https://*.example.org",
        "javascript://example.org",
        "https://example.org:bad",
    ]:
        with pytest.raises(ValueError):
            normalize_origin(invalid)


def test_customer_bearer_is_bound_to_widget_and_not_public_key():
    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from ips.core.config import Settings
    from ips.main import create_app

    app = create_app(Settings(web_origin="http://localhost:5173", cookie_secure=False))
    with TestClient(app) as client:
        admin = client.post(
            "/api/v1/auth/register",
            headers={"Origin": "http://localhost:5173"},
            json={
                "organization_name": "A",
                "email": f"{uuid4()}@example.org",
                "password": "correct-horse-battery",
            },
        )
        assert admin.status_code == 201
        csrf = admin.json()["csrf_token"]
        data = {
            "name": "Widget",
            "color": "#123456",
            "greeting": "Hello",
            "allowed_origins": ["https://example.org"],
            "section_ids": [],
        }
        first = client.post(
            "/api/v1/widgets",
            headers={"Origin": "http://localhost:5173", "X-CSRF-Token": csrf},
            json=data,
        )
        assert first.status_code == 201, first.text
        key = first.json()["public_key"]
        url = f"/api/v1/public/widgets/{key}/sessions"
        assert (
            client.post(
                url, headers={"Origin": "https://example.org.attacker.test"}, json={}
            ).status_code
            == 403
        )
        preflight = client.options(
            url,
            headers={
                "Origin": "https://example.org",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "https://example.org"
        session = client.post(url, headers={"Origin": "https://example.org"}, json={})
        assert session.status_code == 201, session.text
        assert session.headers["access-control-allow-origin"] == "https://example.org"
        assert "access-control-allow-credentials" not in session.headers
        token = session.json()["access_token"]
        assert (
            client.get(
                "/api/v1/public/session", headers={"Authorization": f"Bearer {key}"}
            ).status_code
            == 401
        )
        restored = client.get(
            "/api/v1/public/session", headers={"Authorization": f"Bearer {token}"}
        )
        assert restored.status_code == 200
        assert restored.json()["widget_id"] == first.json()["id"]
        with app.state.engine.connect() as conn:
            stored = conn.execute(
                text("SELECT token_hash FROM customer_sessions WHERE widget_id = :id"),
                {"id": first.json()["id"]},
            ).scalar_one()
            assert stored != token


def test_signed_identity_nonce_cannot_be_reused():
    from uuid import UUID

    from fastapi.testclient import TestClient

    from ips.core.config import Settings
    from ips.main import create_app

    settings = Settings(web_origin="http://localhost:5173", cookie_secure=False)
    app = create_app(settings)
    with TestClient(app) as client:
        admin = client.post(
            "/api/v1/auth/register",
            headers={"Origin": "http://localhost:5173"},
            json={
                "organization_name": "A",
                "email": f"{uuid4()}@example.org",
                "password": "correct-horse-battery",
            },
        ).json()
        data = {
            "name": "Widget",
            "color": "#123456",
            "greeting": "Hello",
            "allowed_origins": ["https://example.org"],
            "section_ids": [],
        }
        widget = client.post(
            "/api/v1/widgets",
            headers={"Origin": "http://localhost:5173", "X-CSRF-Token": admin["csrf_token"]},
            json=data,
        ).json()
        key = widget["public_key"]
        token = signed(derive_widget_secret(settings.session_secret, UUID(widget["id"])), key)
        url = f"/api/v1/public/widgets/{key}/sessions"
        assert (
            client.post(
                url, headers={"Origin": "https://example.org"}, json={"signed_identity": token}
            ).status_code
            == 201
        )
        assert (
            client.post(
                url, headers={"Origin": "https://example.org"}, json={"signed_identity": token}
            ).status_code
            == 401
        )


def test_preflight_rejects_private_network_request():
    from fastapi.testclient import TestClient

    from ips.core.config import Settings
    from ips.main import create_app

    app = create_app(Settings(web_origin="http://localhost:5173", cookie_secure=False))
    with TestClient(app) as client:
        admin = client.post(
            "/api/v1/auth/register",
            headers={"Origin": "http://localhost:5173"},
            json={
                "organization_name": "A",
                "email": f"{uuid4()}@example.org",
                "password": "correct-horse-battery",
            },
        ).json()
        widget = client.post(
            "/api/v1/widgets",
            headers={"Origin": "http://localhost:5173", "X-CSRF-Token": admin["csrf_token"]},
            json={
                "name": "Widget",
                "color": "#123456",
                "greeting": "Hello",
                "allowed_origins": ["https://example.org"],
                "section_ids": [],
            },
        ).json()
        response = client.options(
            f"/api/v1/public/widgets/{widget['public_key']}/sessions",
            headers={
                "Origin": "https://example.org",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
                "Access-Control-Request-Private-Network": "true",
            },
        )
        assert response.status_code == 403
        assert "access-control-allow-private-network" not in response.headers


def test_signing_key_is_admin_only_and_tenant_scoped():
    from fastapi.testclient import TestClient

    from ips.core.config import Settings
    from ips.main import create_app

    settings = Settings(web_origin="http://localhost:5173", cookie_secure=False)
    app = create_app(settings)
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/auth/register",
            headers={"Origin": "http://localhost:5173"},
            json={
                "organization_name": "A",
                "email": f"{uuid4()}@example.org",
                "password": "correct-horse-battery",
            },
        ).json()
        headers = {"Origin": "http://localhost:5173", "X-CSRF-Token": first["csrf_token"]}
        widget = client.post(
            "/api/v1/widgets",
            headers=headers,
            json={
                "name": "Widget",
                "color": "#123456",
                "greeting": "Hello",
                "allowed_origins": ["https://example.org"],
                "section_ids": [],
            },
        ).json()
        endpoint = f"/api/v1/widgets/{widget['id']}/signing-key"
        assert client.post(endpoint, headers={"Origin": "http://localhost:5173"}).status_code == 422
        retrieved = client.post(endpoint, headers=headers)
        assert retrieved.status_code == 200
        assert retrieved.headers["cache-control"] == "no-store"
        encoded = retrieved.json()["key"]
        decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        assert decoded == derive_widget_secret(settings.session_secret, UUID(widget["id"]))
        second = client.post(
            "/api/v1/auth/register",
            headers={"Origin": "http://localhost:5173"},
            json={
                "organization_name": "B",
                "email": f"{uuid4()}@example.org",
                "password": "correct-horse-battery",
            },
        ).json()
        assert (
            client.post(
                endpoint,
                headers={"Origin": "http://localhost:5173", "X-CSRF-Token": second["csrf_token"]},
            ).status_code
            == 404
        )


def test_customer_cannot_supply_unverified_external_identity():
    from fastapi.testclient import TestClient

    from ips.core.config import Settings
    from ips.main import create_app

    app = create_app(Settings(web_origin="http://localhost:5173", cookie_secure=False))
    with TestClient(app) as client:
        admin = client.post(
            "/api/v1/auth/register",
            headers={"Origin": "http://localhost:5173"},
            json={
                "organization_name": "A",
                "email": f"{uuid4()}@example.org",
                "password": "correct-horse-battery",
            },
        ).json()
        widget = client.post(
            "/api/v1/widgets",
            headers={"Origin": "http://localhost:5173", "X-CSRF-Token": admin["csrf_token"]},
            json={
                "name": "Widget",
                "color": "#123456",
                "greeting": "Hello",
                "allowed_origins": ["https://example.org"],
                "section_ids": [],
            },
        ).json()
        url = f"/api/v1/public/widgets/{widget['public_key']}/sessions"
        assert (
            client.post(
                url, headers={"Origin": "https://example.org"}, json={"external_id": "victim"}
            ).status_code
            == 422
        )


def test_customer_principal_uses_persisted_tenant_and_widget():
    from typing import Annotated

    from fastapi import Depends
    from fastapi.testclient import TestClient

    from ips.core.config import Settings
    from ips.core.ports import CustomerPrincipal
    from ips.main import create_app
    from ips.platform.widget_api import customer_principal

    app = create_app(Settings(web_origin="http://localhost:5173", cookie_secure=False))

    @app.get("/test/customer")
    def endpoint(principal: Annotated[CustomerPrincipal, Depends(customer_principal)]):
        return principal.model_dump(mode="json")

    with TestClient(app) as client:
        admin = client.post(
            "/api/v1/auth/register",
            headers={"Origin": "http://localhost:5173"},
            json={
                "organization_name": "A",
                "email": f"{uuid4()}@example.org",
                "password": "correct-horse-battery",
            },
        ).json()
        widget = client.post(
            "/api/v1/widgets",
            headers={"Origin": "http://localhost:5173", "X-CSRF-Token": admin["csrf_token"]},
            json={
                "name": "Widget",
                "color": "#123456",
                "greeting": "Hello",
                "allowed_origins": ["https://example.org"],
                "section_ids": [],
            },
        ).json()
        public = client.post(
            f"/api/v1/public/widgets/{widget['public_key']}/sessions",
            headers={"Origin": "https://example.org"},
            json={},
        ).json()
        principal = client.get(
            "/test/customer", headers={"Authorization": f"Bearer {public['access_token']}"}
        )
        assert principal.status_code == 200
        assert principal.json()["tenant_id"] == admin["tenant_id"]
        assert principal.json()["widget_id"] == widget["id"]
        assert principal.json()["customer_id"] == public["customer_id"]
        assert principal.json()["conversation_scope"] is None
        assert (
            client.get(
                "/test/customer", headers={"Authorization": f"Bearer {widget['public_key']}"}
            ).status_code
            == 401
        )


def test_empty_signed_identity_does_not_silently_become_anonymous():
    from fastapi.testclient import TestClient

    from ips.core.config import Settings
    from ips.main import create_app

    app = create_app(Settings(web_origin="http://localhost:5173", cookie_secure=False))
    with TestClient(app) as client:
        admin = client.post(
            "/api/v1/auth/register",
            headers={"Origin": "http://localhost:5173"},
            json={
                "organization_name": "A",
                "email": f"{uuid4()}@example.org",
                "password": "correct-horse-battery",
            },
        ).json()
        widget = client.post(
            "/api/v1/widgets",
            headers={"Origin": "http://localhost:5173", "X-CSRF-Token": admin["csrf_token"]},
            json={
                "name": "Widget",
                "color": "#123456",
                "greeting": "Hello",
                "allowed_origins": ["https://example.org"],
                "section_ids": [],
            },
        ).json()
        response = client.post(
            f"/api/v1/public/widgets/{widget['public_key']}/sessions",
            headers={"Origin": "https://example.org"},
            json={"signed_identity": ""},
        )
        assert response.status_code == 401


def test_customer_session_is_rejected_after_domain_is_removed():
    from fastapi.testclient import TestClient

    from ips.core.config import Settings
    from ips.main import create_app

    app = create_app(Settings(web_origin="http://localhost:5173", cookie_secure=False))
    with TestClient(app) as client:
        admin = client.post(
            "/api/v1/auth/register",
            headers={"Origin": "http://localhost:5173"},
            json={
                "organization_name": "A",
                "email": f"{uuid4()}@example.org",
                "password": "correct-horse-battery",
            },
        ).json()
        staff_headers = {"Origin": "http://localhost:5173", "X-CSRF-Token": admin["csrf_token"]}
        data = {
            "name": "Widget",
            "color": "#123456",
            "greeting": "Hello",
            "allowed_origins": ["https://example.org"],
            "section_ids": [],
        }
        widget = client.post("/api/v1/widgets", headers=staff_headers, json=data).json()
        session = client.post(
            f"/api/v1/public/widgets/{widget['public_key']}/sessions",
            headers={"Origin": "https://example.org"},
            json={},
        ).json()
        bearer_headers = {"Authorization": f"Bearer {session['access_token']}"}
        assert client.get("/api/v1/public/session", headers=bearer_headers).status_code == 200
        data["allowed_origins"] = ["https://new.example.org"]
        assert (
            client.put(
                f"/api/v1/widgets/{widget['id']}", headers=staff_headers, json=data
            ).status_code
            == 200
        )
        assert client.get("/api/v1/public/session", headers=bearer_headers).status_code == 401


def test_signed_customer_identity_is_stable_only_within_its_tenant():
    from fastapi.testclient import TestClient

    from ips.core.config import Settings
    from ips.main import create_app

    app = create_app(Settings(web_origin="http://localhost:5173", cookie_secure=False))
    with TestClient(app) as client:

        def create_admin(name):
            response = client.post(
                "/api/v1/auth/register",
                headers={"Origin": "http://localhost:5173"},
                json={
                    "organization_name": name,
                    "email": f"{uuid4()}@example.org",
                    "password": "correct-horse-battery",
                },
            )
            assert response.status_code == 201
            return {
                "Origin": "http://localhost:5173",
                "X-CSRF-Token": response.json()["csrf_token"],
            }

        def create_widget_and_identify(headers):
            widget = client.post(
                "/api/v1/widgets",
                headers=headers,
                json={
                    "name": "Widget",
                    "color": "#123456",
                    "greeting": "Hello",
                    "allowed_origins": ["https://example.org"],
                    "section_ids": [],
                },
            ).json()
            key_response = client.post(
                f"/api/v1/widgets/{widget['id']}/signing-key", headers=headers
            )
            assert key_response.status_code == 200
            encoded = key_response.json()["key"]
            secret = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            assertion = signed(secret, widget["public_key"], nonce=str(uuid4()))
            response = client.post(
                f"/api/v1/public/widgets/{widget['public_key']}/sessions",
                headers={"Origin": "https://example.org"},
                json={"signed_identity": assertion},
            )
            assert response.status_code == 201
            return response.json()["customer_id"]

        headers_a = create_admin("Tenant A")
        first = create_widget_and_identify(headers_a)
        second = create_widget_and_identify(headers_a)
        assert first == second
        headers_b = create_admin("Tenant B")
        third = create_widget_and_identify(headers_b)
        assert first != third
