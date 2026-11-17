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
