from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from ips.core.config import Settings
from ips.core.jobs import drain_once
from ips.main import create_app
from ips.platform.notifications import invitation_handler


def test_invitation_and_member_privileges():
    app = create_app(Settings(web_origin="http://localhost:5173", cookie_secure=False))
    emails = []
    with TestClient(app) as client:
        result = client.post(
            "/api/v1/auth/register",
            headers={"Origin": "http://localhost:5173"},
            json={
                "organization_name": "A",
                "email": f"{uuid4()}@example.org",
                "password": "correct-horse-battery",
            },
        )
        admin = result.json()
        csrf = {"Origin": "http://localhost:5173", "X-CSRF-Token": admin["csrf_token"]}
        invite = client.post(
            "/api/v1/members/invitations",
            headers=csrf,
            json={"email": "newperson@example.org", "role": "specialist"},
        )
        assert invite.status_code == 201, invite.text
        with app.state.engine.connect() as conn:
            job = conn.execute(
                text("SELECT payload FROM jobs WHERE tenant_id=:tenant AND kind='invitation'"),
                {"tenant": admin["tenant_id"]},
            ).scalar_one()
            assert "token" not in job and "email" not in job
        handler = invitation_handler(
            app.state.engine,
            app.state.settings,
            send=lambda recipient, url: emails.append((recipient, url)),
        )
        assert drain_once(app.state.engine, {"invitation": handler})
        assert len(emails) == 1 and emails[0][0] == "newperson@example.org"
        token = emails[0][1].split("#token=", 1)[1]
        accepted = client.post(
            "/api/v1/auth/invitations/accept",
            headers={"Origin": "http://localhost:5173"},
            json={"token": token, "password": "a-strong-new-password"},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["role"] == "specialist"
        assert (
            client.post(
                f"/api/v1/widgets/{uuid4()}/signing-key",
                headers={
                    "Origin": "http://localhost:5173",
                    "X-CSRF-Token": accepted.json()["csrf_token"],
                },
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/auth/invitations/accept",
                headers={"Origin": "http://localhost:5173"},
                json={"token": token, "password": "a-strong-new-password"},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/v1/widgets",
                headers={
                    "Origin": "http://localhost:5173",
                    "X-CSRF-Token": accepted.json()["csrf_token"],
                },
                json={
                    "name": "x",
                    "color": "#123456",
                    "greeting": "x",
                    "allowed_origins": [],
                    "section_ids": [],
                },
            ).status_code
            == 403
        )


def test_members_categories_are_tenant_scoped_and_last_admin_is_preserved():
    app = create_app(Settings(web_origin="http://localhost:5173", cookie_secure=False))
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
        first_headers = {"Origin": "http://localhost:5173", "X-CSRF-Token": first["csrf_token"]}
        category = client.post(
            "/api/v1/categories", headers=first_headers, json={"name": "Payments"}
        )
        assert category.status_code == 201
        member_id = first["membership_id"]
        updated = client.patch(
            f"/api/v1/members/{member_id}",
            headers=first_headers,
            json={"available": False, "max_active": 3, "category_ids": [category.json()["id"]]},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["category_ids"] == [category.json()["id"]]
        assert (
            client.patch(
                f"/api/v1/members/{member_id}", headers=first_headers, json={"role": "specialist"}
            ).status_code
            == 409
        )
        second = client.post(
            "/api/v1/auth/register",
            headers={"Origin": "http://localhost:5173"},
            json={
                "organization_name": "B",
                "email": f"{uuid4()}@example.org",
                "password": "correct-horse-battery",
            },
        ).json()
        second_headers = {"Origin": "http://localhost:5173", "X-CSRF-Token": second["csrf_token"]}
        assert (
            client.patch(
                f"/api/v1/members/{member_id}", headers=second_headers, json={"available": True}
            ).status_code
            == 404
        )
        assert (
            client.patch(
                f"/api/v1/members/{second['membership_id']}",
                headers=second_headers,
                json={"category_ids": [category.json()["id"]]},
            ).status_code
            == 404
        )
        assert client.get("/api/v1/categories").json()["items"] == []


def test_concurrent_admin_demotions_keep_one_admin():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from ips.platform.auth import hasher

    app = create_app(Settings(web_origin="http://localhost:5173", cookie_secure=False))
    email = f"{uuid4()}@example.org"
    with TestClient(app) as client_a, TestClient(app) as client_b:
        first = client_a.post(
            "/api/v1/auth/register",
            headers={"Origin": "http://localhost:5173"},
            json={
                "organization_name": "A",
                "email": f"{uuid4()}@example.org",
                "password": "correct-horse-battery",
            },
        ).json()
        second_user, second_member = uuid4(), uuid4()
        with app.state.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (id,normalized_email,password_hash) VALUES (:id,:email,:password)"
                ),
                {
                    "id": second_user,
                    "email": email,
                    "password": hasher.hash("another-strong-password"),
                },
            )
            conn.execute(
                text(
                    "INSERT INTO memberships (id,tenant_id,user_id,role) VALUES (:id,:tenant,:user,'admin')"
                ),
                {"id": second_member, "tenant": first["tenant_id"], "user": second_user},
            )
        second = client_b.post(
            "/api/v1/auth/login",
            headers={"Origin": "http://localhost:5173"},
            json={"email": email, "password": "another-strong-password"},
        ).json()
        barrier = Barrier(2)

        def demote(client, member, csrf):
            barrier.wait(timeout=5)
            return client.patch(
                f"/api/v1/members/{member}",
                headers={"Origin": "http://localhost:5173", "X-CSRF-Token": csrf},
                json={"role": "specialist"},
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [
                pool.submit(demote, client_a, first["membership_id"], first["csrf_token"]),
                pool.submit(demote, client_b, second_member, second["csrf_token"]),
            ]
            statuses = sorted(result.result(timeout=15) for result in results)
        assert statuses == [200, 409]
        with app.state.engine.connect() as conn:
            count = conn.execute(
                text("SELECT count(*) FROM memberships WHERE tenant_id=:tenant AND role='admin'"),
                {"tenant": first["tenant_id"]},
            ).scalar_one()
            assert count == 1


def test_invitation_cannot_take_over_existing_account_and_expired_is_rejected():
    from ips.platform.notifications import invitation_token

    settings = Settings(web_origin="http://localhost:5173", cookie_secure=False)
    app = create_app(settings)
    email = f"{uuid4()}@example.org"
    with TestClient(app) as client_a, TestClient(app) as client_b:
        admin = client_a.post(
            "/api/v1/auth/register",
            headers={"Origin": "http://localhost:5173"},
            json={
                "organization_name": "A",
                "email": f"{uuid4()}@example.org",
                "password": "correct-horse-battery",
            },
        ).json()
        existing = client_b.post(
            "/api/v1/auth/register",
            headers={"Origin": "http://localhost:5173"},
            json={"organization_name": "B", "email": email, "password": "existing-user-password"},
        ).json()
        headers = {"Origin": "http://localhost:5173", "X-CSRF-Token": admin["csrf_token"]}
        invite = client_a.post(
            "/api/v1/members/invitations",
            headers=headers,
            json={"email": email, "role": "specialist"},
        ).json()
        token = invitation_token(settings.session_secret, UUID(invite["invitation_id"]))
        wrong = client_b.post(
            "/api/v1/auth/invitations/accept",
            headers={"Origin": "http://localhost:5173"},
            json={"token": token, "password": "incorrect-long-password"},
        )
        assert wrong.status_code == 401
        assert client_b.get("/api/v1/auth/me").json()["tenant_id"] == existing["tenant_id"]
        correct = client_b.post(
            "/api/v1/auth/invitations/accept",
            headers={"Origin": "http://localhost:5173"},
            json={"token": token, "password": "existing-user-password"},
        )
        assert correct.status_code == 200
        assert correct.json()["user_id"] == existing["user_id"]
        expired = client_a.post(
            "/api/v1/members/invitations",
            headers=headers,
            json={"email": f"{uuid4()}@example.org", "role": "specialist"},
        ).json()
        with app.state.engine.begin() as conn:
            conn.execute(
                text("UPDATE invitations SET expires_at=now() - interval '1 second' WHERE id=:id"),
                {"id": expired["invitation_id"]},
            )
        expired_token = invitation_token(settings.session_secret, UUID(expired["invitation_id"]))
        assert (
            client_b.post(
                "/api/v1/auth/invitations/accept",
                headers={"Origin": "http://localhost:5173"},
                json={"token": expired_token, "password": "long-enough-password"},
            ).status_code
            == 401
        )
