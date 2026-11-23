from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from ips.core.config import Settings
from ips.main import create_app


def test_settings_capabilities_and_audit_through_actual_app():
    app = create_app(Settings())
    with TestClient(app) as client:
        origin = {"Origin": "http://localhost:5173"}
        register = client.post(
            "/api/v1/auth/register",
            headers=origin,
            json={
                "organization_name": "Limits",
                "email": f"{uuid4()}@example.org",
                "password": "correct-horse-battery",
            },
        )
        assert register.status_code == 201, register.text
        headers = {**origin, "X-CSRF-Token": register.json()["csrf_token"]}
        initial = client.get("/api/v1/organization")
        assert initial.status_code == 200, initial.text
        assert initial.json()["name"] == "Limits"
        assert (
            client.patch(
                "/api/v1/organization",
                headers={**origin, "X-CSRF-Token": "invalid"},
                json={"name": "New"},
            ).status_code
            == 403
        )
        changed = client.patch(
            "/api/v1/organization", headers=headers, json={"name": "New", "language": "ru"}
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["name"] == "New" and changed.json()["language"] == "ru"
        updated_member = client.patch(
            f"/api/v1/members/{register.json()['membership_id']}",
            headers=headers,
            json={"available": False},
        )
        assert updated_member.status_code == 200
        capabilities = client.get("/api/v1/capabilities")
        assert capabilities.status_code == 200
        assert capabilities.json()["features"] == ["txt_import"]
        audit = client.get("/api/v1/audit")
        assert audit.status_code == 200 and any(
            item["action"] == "organization.update" for item in audit.json()["items"]
        )
        assert any(item["action"] == "member.update" for item in audit.json()["items"])
        assert "correct-horse-battery" not in audit.text and "@example.org" not in audit.text


def test_two_reservations_last_slot_and_failure_release():
    from ips.core.ports import Usage
    from ips.core.usage import PostgresUsageBudget

    engine = create_engine(Settings().database_url)
    sessions = sessionmaker(engine)
    tenant = uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO organizations (id,name,settings) VALUES (:id,'Budget',CAST(:settings AS jsonb))"
            ),
            {"id": tenant, "settings": '{"ai_daily_limit": 1}'},
        )
    budget = PostgresUsageBudget(sessions)
    start = Barrier(2, timeout=5)

    def reserve(_):
        start.wait()
        try:
            return budget.reserve(tenant, uuid4())
        except HTTPException as exc:
            assert exc.status_code == 429
            return None

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(reserve, range(2)))
        winners = [item for item in result if item is not None]
        assert len(winners) == 1
        budget.settle(winners[0], Usage(input_tokens=1, output_tokens=1), failed=True)
        budget.settle(winners[0], Usage(input_tokens=1, output_tokens=1), failed=True)
        assert budget.reserve(tenant, uuid4()) is not None
    finally:
        engine.dispose()


def test_widgets_enforce_limit_across_simultaneous_creates():
    from ips.core.ports import TenantContext
    from ips.platform.widget_api import WidgetInput, create_widget

    engine = create_engine(Settings().database_url)
    tenant, user, member = uuid4(), uuid4(), uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO organizations (id,name,settings) VALUES (:id,'Widgets',CAST(:settings AS jsonb))"
            ),
            {"id": tenant, "settings": '{"max_widgets": 1}'},
        )
    ctx = TenantContext(tenant_id=tenant, user_id=user, membership_id=member, role="admin")
    body = WidgetInput(
        name="Widget", color="#123456", greeting="Hi", allowed_origins=[], section_ids=[]
    )
    start = Barrier(2, timeout=5)

    def create(_):
        with sessionmaker(engine)() as db:
            start.wait()
            try:
                return create_widget(body, ctx, db).id
            except HTTPException as exc:
                db.rollback()
                assert exc.status_code == 429
                return None

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, range(2)))
        assert len([result for result in results if result is not None]) == 1
    finally:
        engine.dispose()


def test_accepting_invitation_enforces_staff_limit():
    from uuid import UUID

    from ips.platform.notifications import invitation_token

    settings = Settings()
    app = create_app(settings)
    origin = {"Origin": "http://localhost:5173"}
    with TestClient(app) as admin_client, TestClient(app) as invited_client:
        admin = admin_client.post(
            "/api/v1/auth/register",
            headers=origin,
            json={
                "organization_name": "Staff limit",
                "email": f"{uuid4()}@example.org",
                "password": "correct-horse-battery",
            },
        ).json()
        headers = {**origin, "X-CSRF-Token": admin["csrf_token"]}
        with app.state.engine.begin() as conn:
            conn.execute(
                text("UPDATE organizations SET settings=CAST(:settings AS jsonb) WHERE id=:id"),
                {"settings": '{"max_staff": 1}', "id": UUID(admin["tenant_id"])},
            )
        invite = admin_client.post(
            "/api/v1/members/invitations",
            headers=headers,
            json={"email": f"{uuid4()}@example.org", "role": "specialist"},
        )
        assert invite.status_code == 201, invite.text
        token = invitation_token(settings.session_secret, UUID(invite.json()["invitation_id"]))
        response = invited_client.post(
            "/api/v1/auth/invitations/accept",
            headers=origin,
            json={"token": token, "password": "correct-horse-battery"},
        )
        assert response.status_code == 429
        with app.state.engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM memberships WHERE tenant_id=:tenant"),
                    {"tenant": UUID(admin["tenant_id"])},
                ).scalar_one()
                == 1
            )
        with app.state.engine.begin() as conn:
            conn.execute(
                text("DELETE FROM jobs WHERE tenant_id=:tenant"),
                {"tenant": UUID(admin["tenant_id"])},
            )


def test_rate_limiter_is_shared_across_instances_and_does_not_store_identifiers():
    from ips.core.rate_limit import PostgresRateLimiter

    engine = create_engine(Settings().database_url)
    sessions = sessionmaker(engine)
    first = PostgresRateLimiter(sessions, max_requests=2)
    second = PostgresRateLimiter(sessions, max_requests=2)
    client = f"client-{uuid4()}@example.invalid"
    assert first.allow("login", client)
    assert second.allow("login", client)
    assert not first.allow("login", client)
    with engine.connect() as conn:
        stored = conn.execute(text("SELECT bucket_key FROM request_limits")).scalars().all()
        assert all(client not in value for value in stored)
    engine.dispose()


def test_reservation_expiry_idempotent_settlement_and_tenant_isolation():
    from ips.core.ports import Reservation, Usage
    from ips.core.usage import PostgresUsageBudget

    engine = create_engine(Settings().database_url)
    sessions = sessionmaker(engine)
    tenant, foreign = uuid4(), uuid4()
    with engine.begin() as conn:
        for tenant_id in (tenant, foreign):
            conn.execute(
                text(
                    "INSERT INTO organizations (id,name,settings) VALUES (:id,'Quota',CAST(:settings AS jsonb))"
                ),
                {"id": tenant_id, "settings": '{"ai_daily_limit": 1}'},
            )
    budget = PostgresUsageBudget(sessions)
    expired = budget.reserve(tenant, uuid4())
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE usage_reservations SET expires_at=now()-interval '1 second' WHERE id=:id"),
            {"id": expired.id},
        )
    current = budget.reserve(tenant, uuid4())
    fake_foreign = Reservation(id=current.id, tenant_id=foreign, run_id=current.run_id)
    budget.settle(fake_foreign, Usage(input_tokens=9, output_tokens=9), failed=False)
    budget.settle(current, Usage(input_tokens=2, output_tokens=3), failed=False)
    budget.settle(current, Usage(input_tokens=99, output_tokens=99), failed=False)
    with engine.connect() as conn:
        old = conn.execute(
            text("SELECT state FROM usage_reservations WHERE id=:id"), {"id": expired.id}
        ).scalar_one()
        result = conn.execute(
            text("SELECT state,input_tokens,output_tokens FROM usage_reservations WHERE id=:id"),
            {"id": current.id},
        ).one()
    assert old == "expired" and tuple(result) == ("succeeded", 2, 3)
    with pytest.raises(HTTPException) as error:
        budget.reserve(tenant, uuid4())
    assert error.value.status_code == 429
    assert budget.reserve(foreign, uuid4()).tenant_id == foreign
    engine.dispose()


def test_http_login_rate_limit_returns_429():
    from ips.core.rate_limit import PostgresRateLimiter

    app = create_app(Settings())
    app.state.rate_limiter = PostgresRateLimiter(sessionmaker(app.state.engine), max_requests=2)
    with TestClient(app, client=(str(uuid4()), 1234)) as client:
        origin_url = "http://localhost:5173"
        headers = {"Origin": origin_url}
        body = {"email": f"{uuid4()}@example.org", "password": "incorrect"}
        assert client.post("/api/v1/auth/login", headers=headers, json=body).status_code == 401
        assert client.post("/api/v1/auth/login", headers=headers, json=body).status_code == 401
        limited = client.post("/api/v1/auth/login", headers=headers, json=body)
        assert limited.status_code == 429
        assert limited.headers["access-control-allow-origin"] == origin_url


def test_simultaneous_invitation_accepts_respect_staff_limit():
    from uuid import UUID

    from ips.platform.notifications import invitation_token

    settings = Settings()
    app = create_app(settings)
    origin = {"Origin": "http://localhost:5173"}
    with TestClient(app) as admin_client, TestClient(app) as first, TestClient(app) as second:
        admin = admin_client.post(
            "/api/v1/auth/register",
            headers=origin,
            json={
                "organization_name": "Staff race",
                "email": f"{uuid4()}@example.org",
                "password": "correct-horse-battery",
            },
        ).json()
        headers = {**origin, "X-CSRF-Token": admin["csrf_token"]}
        tenant = UUID(admin["tenant_id"])
        with app.state.engine.begin() as conn:
            conn.execute(
                text("UPDATE organizations SET settings=CAST(:settings AS jsonb) WHERE id=:id"),
                {"settings": '{"max_staff": 2}', "id": tenant},
            )
        tokens = []
        for _ in range(2):
            invitation = admin_client.post(
                "/api/v1/members/invitations",
                headers=headers,
                json={"email": f"{uuid4()}@example.org", "role": "specialist"},
            )
            assert invitation.status_code == 201, invitation.text
            tokens.append(
                invitation_token(settings.session_secret, UUID(invitation.json()["invitation_id"]))
            )
        barrier = Barrier(2, timeout=5)

        def accept(pair):
            client, token = pair
            barrier.wait()
            return client.post(
                "/api/v1/auth/invitations/accept",
                headers=origin,
                json={"token": token, "password": "correct-horse-battery"},
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(accept, zip((first, second), tokens, strict=True)))
        assert sorted(outcomes) == [200, 429]
        with app.state.engine.begin() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM memberships WHERE tenant_id=:id"), {"id": tenant}
                ).scalar_one()
                == 2
            )
            conn.execute(text("DELETE FROM jobs WHERE tenant_id=:id"), {"id": tenant})


def test_audit_is_tenant_scoped_and_excludes_personal_data():
    app = create_app(Settings())
    origin = {"Origin": "http://localhost:5173"}
    with TestClient(app) as first, TestClient(app) as second:
        admins = []
        for client in (first, second):
            response = client.post(
                "/api/v1/auth/register",
                headers=origin,
                json={
                    "organization_name": "Audit",
                    "email": f"{uuid4()}@example.org",
                    "password": "correct-horse-battery",
                },
            )
            assert response.status_code == 201
            admins.append(response.json())
        for client, admin in zip((first, second), admins, strict=True):
            headers = {**origin, "X-CSRF-Token": admin["csrf_token"]}
            assert (
                client.patch(
                    "/api/v1/organization", headers=headers, json={"name": "private@example.org"}
                ).status_code
                == 200
            )
        for client, admin in zip((first, second), admins, strict=True):
            audit = client.get("/api/v1/audit")
            assert audit.status_code == 200
            assert len(audit.json()["items"]) == 1
            assert audit.json()["items"][0]["object_id"] == admin["tenant_id"]
            assert "private@example.org" not in audit.text
            assert "correct-horse-battery" not in audit.text


def test_default_composition_exposes_real_usage_budget():
    from ips.core.ports import UsageBudget

    app = create_app(Settings())
    assert isinstance(app.state.usage_budget, UsageBudget)
    assert app.state.ports.usage_budget is app.state.usage_budget
