from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from ips.core.config import Settings
from ips.main import create_app
from ips.tickets.sla import SupportPolicy, WorkWindow, sla_breaches, working_time


def test_schedule_counts_actual_minutes_on_dst_gap_and_repeated_hour():
    policy = SupportPolicy(
        timezone="Europe/Warsaw",
        working_hours=[WorkWindow(day_of_week=6, start="02:10", end="02:40")],
        first_response_minutes=60,
        resolution_minutes=480,
        urgent_first_response_minutes=15,
    )
    spring = datetime(2025, 3, 30, tzinfo=UTC)
    autumn = datetime(2025, 10, 26, tzinfo=UTC)
    assert working_time(policy, spring, spring + timedelta(hours=5)) == timedelta(0)
    assert working_time(policy, autumn, autumn + timedelta(hours=5)) == timedelta(hours=1)
    assert working_time(
        policy, autumn + timedelta(minutes=35), autumn + timedelta(hours=1, minutes=15)
    ) == timedelta(minutes=10)


def test_policy_requires_admin_and_csrf_and_keeps_tenants_isolated():
    settings = Settings()
    app = create_app(settings)
    path = "/api/v1/organization/support-policy"
    origin = {"Origin": settings.web_origin}
    with TestClient(app) as alpha, TestClient(app) as beta:
        assert alpha.get(path).status_code == 401
        for client in (alpha, beta):
            response = client.post(
                "/api/v1/auth/register",
                headers=origin,
                json={
                    "email": f"{uuid4()}@example.org",
                    "password": "just-a-synthetic-password",
                    "organization_name": "SLA test",
                },
            )
            assert response.status_code == 201
            if client is alpha:
                csrf = {"X-CSRF-Token": response.json()["csrf_token"], **origin}
        default = beta.get(path)
        assert default.status_code == 200
        body = {
            **default.json(),
            "timezone": "Europe/Warsaw",
            "working_hours": [
                {"day_of_week": 0, "start": "09:00", "end": "17:00"},
            ],
        }
        assert alpha.put(path, headers=origin, json=body).status_code == 422
        assert (
            alpha.put(path, headers={**origin, "X-CSRF-Token": "invalid"}, json=body).status_code
            == 403
        )
        for invalid in (
            {**body, "timezone": "../UTC"},
            {**body, "first_response_minutes": True},
            {**body, "working_hours": [{"day_of_week": 0, "start": "25:00", "end": "26:00"}]},
            {
                **body,
                "working_hours": [
                    *body["working_hours"],
                    {"day_of_week": 0, "start": "16:00", "end": "18:00"},
                ],
            },
        ):
            assert alpha.put(path, headers=csrf, json=invalid).status_code == 422
        updated = alpha.put(path, headers=csrf, json=body)
        assert updated.status_code == 200 and updated.json() == body
        assert alpha.get(path).json() == body
        assert beta.get(path).json() == default.json()
        with app.state.engine.begin() as conn:
            conn.execute(
                text("UPDATE memberships SET role='specialist' WHERE tenant_id=:tenant"),
                {"tenant": alpha.get("/api/v1/auth/me").json()["tenant_id"]},
            )
        assert alpha.get(path).status_code == 403


def test_sla_breaches_include_only_working_time_and_use_urgent_target():
    policy = SupportPolicy(
        timezone="UTC",
        working_hours=[WorkWindow(day_of_week=0, start="09:00", end="17:00")],
        first_response_minutes=60,
        resolution_minutes=480,
        urgent_first_response_minutes=15,
    )
    start = datetime(2026, 11, 20, 19, tzinfo=UTC)
    monday = datetime(2026, 11, 23, 9, 16, tzinfo=UTC)
    assert sla_breaches(
        policy,
        created_at=start,
        first_response_at=None,
        resolved_at=None,
        urgent=True,
        now=monday,
    ) == (True, False)
    assert sla_breaches(
        policy,
        created_at=start,
        first_response_at=monday,
        resolved_at=None,
        urgent=False,
        now=monday,
    ) == (False, False)
