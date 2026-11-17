from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ips.core.config import Settings
from ips.core.ports import ConversationState, CustomerPrincipal
from ips.tickets.service import TicketService


class FakeConversations:
    def __init__(self, state):
        self.state = state
        self.confirmed = []

    def lock_for_handoff(self, uow, tenant_id, conversation_id):
        if (tenant_id, conversation_id) != (self.state.tenant_id, self.state.id):
            raise HTTPException(404, "Conversation unavailable")
        return self.state

    def mark_human(self, uow, state):
        self.state = state.model_copy(update={"mode": "human"})

    def confirm_human(self, uow, tenant_id, conversation_id):
        self.confirmed.append((tenant_id, conversation_id))


@pytest.fixture
def context():
    engine = create_engine(Settings().database_url)
    tenant, customer, widget, conversation = (uuid4() for _ in range(4))
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO organizations (id, name) VALUES (:id, 'B04 test')"), {"id": tenant}
        )
    principal = CustomerPrincipal(tenant_id=tenant, customer_id=customer, widget_id=widget)
    fake = FakeConversations(
        ConversationState(
            id=conversation,
            tenant_id=tenant,
            customer_id=customer,
            widget_id=widget,
            mode="bot",
            version=1,
        )
    )
    yield engine, principal, fake
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM tickets WHERE tenant_id=:tenant"), {"tenant": tenant})
        conn.execute(text("DELETE FROM organizations WHERE id=:tenant"), {"tenant": tenant})
    engine.dispose()


def test_handoff_is_idempotent_and_sets_human_mode(context):
    engine, principal, fake = context
    service = TicketService(fake)
    with Session(engine) as uow, uow.begin():
        first = service.escalate(uow, principal, fake.state.id, "Help", None, "normal")
        second = service.escalate(uow, principal, fake.state.id, "Again", None, "normal")
        assert first.id == second.id
        assert first.status == "waiting_specialist"
        assert fake.state.mode == "human"
        assert service.has_active(uow, principal.tenant_id, fake.state.id)


def test_foreign_customer_and_conversation_scope_are_hidden(context):
    engine, principal, fake = context
    service = TicketService(fake)
    for invalid in (
        principal.model_copy(update={"customer_id": uuid4()}),
        principal.model_copy(update={"conversation_scope": uuid4()}),
        principal.model_copy(update={"widget_id": uuid4()}),
    ):
        with Session(engine) as uow, pytest.raises(HTTPException) as error:
            service.escalate(uow, invalid, fake.state.id, "Help", None, "normal")
        assert error.value.status_code == 404


def test_reply_records_first_timestamp_and_customer_message_reopens_waiting_ticket(context):
    from datetime import UTC, datetime, timedelta

    from ips.core.ports import TenantContext

    engine, principal, fake = context
    service = TicketService(fake)
    ctx = TenantContext(
        tenant_id=principal.tenant_id, user_id=uuid4(), membership_id=uuid4(), role="specialist"
    )
    timestamp = datetime.now(UTC)
    with Session(engine) as uow, uow.begin():
        ticket = service.escalate(uow, principal, fake.state.id, "Help", None, "normal")
        uow.execute(
            text("UPDATE tickets SET status='waiting_customer' WHERE id=:id"), {"id": ticket.id}
        )
        service.record_staff_reply(uow, ctx, fake.state.id, timestamp)
        service.record_staff_reply(uow, ctx, fake.state.id, timestamp + timedelta(hours=1))
        service.on_customer_message(uow, principal, fake.state.id)
        row = (
            uow.execute(
                text("SELECT status,version,first_staff_reply_at FROM tickets WHERE id=:id"),
                {"id": ticket.id},
            )
            .mappings()
            .one()
        )
        assert row["status"] == "in_progress"
        assert row["version"] == 2
        assert row["first_staff_reply_at"] == timestamp


def test_closed_ticket_does_not_block_new_handoff(context):
    engine, principal, fake = context
    service = TicketService(fake)
    with Session(engine) as uow, uow.begin():
        first = service.escalate(uow, principal, fake.state.id, "Help", None, "normal")
        uow.execute(text("UPDATE tickets SET status='closed' WHERE id=:id"), {"id": first.id})
        second = service.escalate(uow, principal, fake.state.id, "Help again", None, "normal")
        assert first.id != second.id
        assert second.status == "waiting_specialist"


def test_database_enforces_one_active_ticket_per_conversation(context):
    from sqlalchemy.exc import IntegrityError

    engine, principal, fake = context
    service = TicketService(fake)
    with Session(engine) as uow, uow.begin():
        service.escalate(uow, principal, fake.state.id, "Help", None, "normal")
        with pytest.raises(IntegrityError), uow.begin_nested():
            uow.execute(
                text(
                    "INSERT INTO tickets (id, tenant_id, conversation_id, status, priority) VALUES (:id, :tenant, :conversation, 'in_progress', 'normal')"
                ),
                {"id": uuid4(), "tenant": principal.tenant_id, "conversation": fake.state.id},
            )


def test_staff_status_requires_current_version_and_valid_transition(context):
    from ips.core.ports import TenantContext

    engine, principal, fake = context
    service = TicketService(fake)
    admin = TenantContext(
        tenant_id=principal.tenant_id, user_id=uuid4(), membership_id=uuid4(), role="admin"
    )
    with Session(engine) as uow, uow.begin():
        ticket = service.escalate(uow, principal, fake.state.id, "Help", None, "normal")
        with pytest.raises(HTTPException) as error:
            service.update_status(uow, admin, ticket.id, ticket.version, "resolved")
        assert error.value.status_code == 409
        uow.execute(text("UPDATE tickets SET status='in_progress' WHERE id=:id"), {"id": ticket.id})
        updated = service.update_status(uow, admin, ticket.id, ticket.version, "resolved")
        assert updated.status == "resolved"
        assert updated.version == ticket.version + 1
        with pytest.raises(HTTPException) as error:
            service.update_status(uow, admin, ticket.id, ticket.version, "waiting_customer")
        assert error.value.status_code == 409


def test_staff_updates_require_assignee_or_admin_and_same_tenant(context):
    from ips.core.ports import TenantContext

    engine, principal, fake = context
    service = TicketService(fake)
    specialist = TenantContext(
        tenant_id=principal.tenant_id, user_id=uuid4(), membership_id=uuid4(), role="specialist"
    )
    foreign = specialist.model_copy(update={"tenant_id": uuid4(), "role": "admin"})
    with Session(engine) as uow, uow.begin():
        ticket = service.escalate(uow, principal, fake.state.id, "Help", None, "normal")
        uow.execute(text("UPDATE tickets SET status='in_progress' WHERE id=:id"), {"id": ticket.id})
        for ctx, expected in ((specialist, 403), (foreign, 404)):
            with pytest.raises(HTTPException) as error:
                service.update_status(uow, ctx, ticket.id, ticket.version, "resolved")
            assert error.value.status_code == expected


def test_customer_reopen_and_confirm_enforce_ownership_and_active_ticket(context):
    engine, principal, fake = context
    service = TicketService(fake)
    with Session(engine) as uow, uow.begin():
        first = service.escalate(uow, principal, fake.state.id, "Help", None, "normal")
        uow.execute(text("UPDATE tickets SET status='resolved' WHERE id=:id"), {"id": first.id})
        foreign = principal.model_copy(update={"customer_id": uuid4()})
        with pytest.raises(HTTPException) as error:
            service.reopen(uow, foreign, first.id)
        assert error.value.status_code == 404
        second = service.escalate(uow, principal, fake.state.id, "More help", None, "normal")
        with pytest.raises(HTTPException) as error:
            service.reopen(uow, principal, first.id)
        assert error.value.status_code == 409
        uow.execute(text("UPDATE tickets SET status='closed' WHERE id=:id"), {"id": second.id})
        reopened = service.reopen(uow, principal, first.id)
        assert reopened.id == first.id and reopened.status == "in_progress"
        uow.execute(text("UPDATE tickets SET status='resolved' WHERE id=:id"), {"id": first.id})
        closed = service.confirm(uow, principal, first.id)
        assert closed.status == "closed"
        assert fake.confirmed == [(principal.tenant_id, fake.state.id)]
        with pytest.raises(HTTPException) as error:
            service.reopen(uow, principal, first.id)
        assert error.value.status_code == 409


def test_public_ticket_http_uses_authenticated_customer_and_port(context):
    from fastapi.testclient import TestClient

    from ips.main import create_app
    from ips.platform.widget_api import customer_principal

    _, principal, fake = context
    app = create_app(Settings(), ports=type("Ports", (), {"conversations": fake})())
    app.dependency_overrides[customer_principal] = lambda: principal
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/public/conversations/{fake.state.id}/handoff", json={"reason": "Help"}
        )
        assert response.status_code == 200, response.text
        ticket = response.json()
        assert ticket["status"] == "waiting_specialist"
        assert client.get(f"/api/v1/public/tickets/{ticket['id']}").json()["id"] == ticket["id"]
        foreign = principal.model_copy(update={"customer_id": uuid4()})
        app.dependency_overrides[customer_principal] = lambda: foreign
        assert client.get(f"/api/v1/public/tickets/{ticket['id']}").status_code == 404
        app.dependency_overrides[customer_principal] = lambda: principal
        assert client.post(f"/api/v1/public/tickets/{ticket['id']}/confirm").status_code == 409


def test_ticket_http_requires_authentication_and_unavailable_port_fails_closed(context):
    from fastapi.testclient import TestClient

    from ips.main import create_app
    from ips.platform.widget_api import customer_principal

    _, principal, fake = context
    app = create_app(Settings())
    with TestClient(app) as client:
        url = f"/api/v1/public/conversations/{fake.state.id}/handoff"
        assert client.post(url, json={"reason": "Help"}).status_code == 401
        app.dependency_overrides[customer_principal] = lambda: principal
        assert client.post(url, json={"reason": "Help"}).status_code == 503


def test_widget_ticket_cors_allows_only_configured_widget_origin(context):
    from fastapi.testclient import TestClient

    from ips.main import create_app

    _, _, fake = context
    app = create_app(Settings(widget_origin="https://widget.example"))
    path = f"/api/v1/public/conversations/{fake.state.id}/handoff"
    with TestClient(app) as client:
        headers = {
            "Origin": "https://widget.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        }
        allowed = client.options(path, headers=headers)
        assert allowed.status_code == 200
        assert allowed.headers["access-control-allow-origin"] == "https://widget.example"
        assert "access-control-allow-credentials" not in allowed.headers
        headers["Origin"] = "https://attacker.example"
        denied = client.options(path, headers=headers)
        assert denied.status_code == 403
        assert "access-control-allow-origin" not in denied.headers


def test_staff_get_ticket_is_tenant_scoped(context):
    from fastapi.testclient import TestClient

    from ips.core.ports import TenantContext
    from ips.main import create_app
    from ips.platform.auth import tenant_context

    engine, principal, fake = context
    service = TicketService(fake)
    with Session(engine) as uow, uow.begin():
        ticket = service.escalate(uow, principal, fake.state.id, "Help", None, "normal")
    own = TenantContext(
        tenant_id=principal.tenant_id, user_id=uuid4(), membership_id=uuid4(), role="admin"
    )
    foreign = own.model_copy(update={"tenant_id": uuid4()})
    app = create_app(Settings())
    with TestClient(app) as client:
        assert client.get(f"/api/v1/tickets/{ticket.id}").status_code == 401
        app.dependency_overrides[tenant_context] = lambda: own
        assert client.get(f"/api/v1/tickets/{ticket.id}").json()["id"] == str(ticket.id)
        app.dependency_overrides[tenant_context] = lambda: foreign
        assert client.get(f"/api/v1/tickets/{ticket.id}").status_code == 404


def test_staff_list_ticket_paginates_and_filters_by_tenant(context):
    from fastapi.testclient import TestClient

    from ips.core.ports import TenantContext
    from ips.main import create_app
    from ips.platform.auth import tenant_context

    engine, principal, fake = context
    service = TicketService(fake)
    with Session(engine) as uow, uow.begin():
        ticket = service.escalate(uow, principal, fake.state.id, "Help", None, "normal")
    own = TenantContext(
        tenant_id=principal.tenant_id, user_id=uuid4(), membership_id=uuid4(), role="admin"
    )
    app = create_app(Settings())
    app.dependency_overrides[tenant_context] = lambda: own
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/tickets", params={"status": "waiting_specialist", "limit": 1}
        )
        assert response.status_code == 200
        assert [x["id"] for x in response.json()["items"]] == [str(ticket.id)]
        assert client.get("/api/v1/tickets", params={"cursor": "not-a-uuid"}).status_code == 422
        app.dependency_overrides[tenant_context] = lambda: own.model_copy(
            update={"tenant_id": uuid4()}
        )
        assert client.get("/api/v1/tickets").json()["items"] == []


def test_staff_update_status_requires_csrf_and_rejects_stale_version(context):
    from fastapi.testclient import TestClient

    from ips.core.ports import TenantContext
    from ips.main import create_app
    from ips.platform.auth import require_csrf, tenant_context

    engine, principal, fake = context
    service = TicketService(fake)
    with Session(engine) as uow, uow.begin():
        ticket = service.escalate(uow, principal, fake.state.id, "Help", None, "normal")
        uow.execute(text("UPDATE tickets SET status='in_progress' WHERE id=:id"), {"id": ticket.id})
    ctx = TenantContext(
        tenant_id=principal.tenant_id, user_id=uuid4(), membership_id=uuid4(), role="admin"
    )
    app = create_app(Settings())
    app.dependency_overrides[tenant_context] = lambda: ctx
    with TestClient(app) as client:
        path = f"/api/v1/tickets/{ticket.id}"
        body = {"expected_version": 1, "status": "resolved"}
        assert client.put(path, json=body).status_code in (401, 422)
        app.dependency_overrides[require_csrf] = lambda: None
        result = client.put(path, json=body)
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "resolved"
        assert client.put(path, json=body).status_code == 409
        assert client.put(path, json={"expected_version": 2, "priority": "high"}).status_code == 503


def test_public_handoff_authenticates_real_customer_bearer():
    from uuid import UUID

    from fastapi.testclient import TestClient

    from ips.main import create_app

    app = create_app(Settings())
    with TestClient(app) as client:
        admin = client.post(
            "/api/v1/auth/register",
            headers={"Origin": "http://localhost:5173"},
            json={
                "organization_name": "HTTP auth",
                "email": f"{uuid4()}@example.org",
                "password": "correct-horse-battery",
            },
        )
        assert admin.status_code == 201
        widget = client.post(
            "/api/v1/widgets",
            headers={"Origin": "http://localhost:5173", "X-CSRF-Token": admin.json()["csrf_token"]},
            json={
                "name": "Widget",
                "color": "#123456",
                "greeting": "Hello",
                "allowed_origins": ["https://example.org"],
                "section_ids": [],
            },
        )
        assert widget.status_code == 201
        session_url = f"/api/v1/public/widgets/{widget.json()['public_key']}/sessions"
        session = client.post(session_url, headers={"Origin": "https://example.org"}, json={})
        assert session.status_code == 201
        token = session.json()["access_token"]
        conversation = uuid4()
        state = ConversationState(
            id=conversation,
            tenant_id=UUID(admin.json()["tenant_id"]),
            customer_id=UUID(session.json()["customer_id"]),
            widget_id=UUID(session.json()["widget_id"]),
            mode="bot",
            version=1,
        )
        app.state.ports = type("Ports", (), {"conversations": FakeConversations(state)})()
        path = f"/api/v1/public/conversations/{conversation}/handoff"
        assert (
            client.post(
                path, headers={"Authorization": "Bearer invalid"}, json={"reason": "Help"}
            ).status_code
            == 401
        )
        result = client.post(
            path,
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": app.state.settings.widget_origin,
            },
            json={"reason": "Help"},
        )
        assert result.status_code == 200, result.text
        assert result.headers["access-control-allow-origin"] == app.state.settings.widget_origin
        assert "access-control-allow-credentials" not in result.headers
