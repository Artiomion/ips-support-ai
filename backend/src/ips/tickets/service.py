"""Ticket domain operations. All changes use the caller's transaction."""

from datetime import datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from ips.core.ports import ConversationPort, CustomerPrincipal, TenantContext, TicketSummary


class TicketService:
    def __init__(self, conversations: ConversationPort):
        self.conversations = conversations

    def escalate(
        self,
        uow: Session,
        principal: CustomerPrincipal,
        conversation_id: UUID,
        reason: str,
        category: str | None,
        priority: str,
    ) -> TicketSummary:
        if (
            principal.conversation_scope is not None
            and principal.conversation_scope != conversation_id
        ):
            raise HTTPException(404, "Conversation unavailable")
        state = self.conversations.lock_for_handoff(uow, principal.tenant_id, conversation_id)
        if (state.tenant_id, state.customer_id, state.widget_id) != (
            principal.tenant_id,
            principal.customer_id,
            principal.widget_id,
        ):
            raise HTTPException(404, "Conversation unavailable")
        if priority not in ("low", "normal", "high", "urgent"):
            raise ValueError("Invalid priority")
        row = (
            uow.execute(
                text("""SELECT * FROM tickets WHERE tenant_id=:tenant AND conversation_id=:conversation
            AND status NOT IN ('resolved', 'closed') FOR UPDATE"""),
                {"tenant": principal.tenant_id, "conversation": conversation_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            category_id = None
            if category:
                category_id = uow.execute(
                    text("SELECT id FROM categories WHERE tenant_id=:tenant AND name=:name"),
                    {"tenant": principal.tenant_id, "name": category},
                ).scalar_one_or_none()
            row = (
                uow.execute(
                    text("""INSERT INTO tickets (id,tenant_id,conversation_id,status,priority,category_id)
                VALUES (:id,:tenant,:conversation,'waiting_specialist',:priority,:category) RETURNING *"""),
                    {
                        "id": uuid4(),
                        "tenant": principal.tenant_id,
                        "conversation": conversation_id,
                        "priority": priority,
                        "category": category_id,
                    },
                )
                .mappings()
                .one()
            )
        self.conversations.mark_human(uow, state)
        return TicketSummary.model_validate({key: row[key] for key in TicketSummary.model_fields})

    def has_active(self, uow: Session, tenant_id: UUID, conversation_id: UUID) -> bool:
        return bool(
            uow.execute(
                text("""SELECT EXISTS(SELECT 1 FROM tickets WHERE tenant_id=:tenant
            AND conversation_id=:conversation AND status NOT IN ('resolved','closed'))"""),
                {"tenant": tenant_id, "conversation": conversation_id},
            ).scalar_one()
        )

    def on_customer_message(
        self, uow: Session, principal: CustomerPrincipal, conversation_id: UUID
    ) -> None:
        state = self.conversations.lock_for_handoff(uow, principal.tenant_id, conversation_id)
        if (state.customer_id, state.widget_id) != (principal.customer_id, principal.widget_id) or (
            principal.conversation_scope and principal.conversation_scope != conversation_id
        ):
            raise HTTPException(404, "Conversation unavailable")
        uow.execute(
            text("""UPDATE tickets SET status='in_progress',version=version+1,updated_at=now()
            WHERE tenant_id=:tenant AND conversation_id=:conversation AND status='waiting_customer'"""),
            {"tenant": principal.tenant_id, "conversation": conversation_id},
        )

    def record_staff_reply(
        self, uow: Session, ctx: TenantContext, conversation_id: UUID, created_at: datetime
    ) -> None:
        uow.execute(
            text("""UPDATE tickets SET first_staff_reply_at=:created WHERE tenant_id=:tenant
            AND conversation_id=:conversation AND status NOT IN ('resolved','closed') AND first_staff_reply_at IS NULL"""),
            {"created": created_at, "tenant": ctx.tenant_id, "conversation": conversation_id},
        )

    def _ticket(self, uow: Session, tenant_id: UUID, ticket_id: UUID, *, lock: bool = False):
        query = "SELECT * FROM tickets WHERE tenant_id=:tenant AND id=:id"
        if lock:
            query += " FOR UPDATE"
        row = uow.execute(text(query), {"tenant": tenant_id, "id": ticket_id}).mappings().first()
        if row is None:
            raise HTTPException(404, "Ticket unavailable")
        return row

    def update_status(
        self, uow: Session, ctx: TenantContext, ticket_id: UUID, expected_version: int, status: str
    ) -> TicketSummary:
        row = self._ticket(uow, ctx.tenant_id, ticket_id, lock=True)
        if ctx.role != "admin" and row["assignee_id"] != ctx.membership_id:
            raise HTTPException(403, "Ticket is assigned to another specialist")
        transitions = {
            "in_progress": {"waiting_customer", "resolved"},
            "waiting_customer": {"in_progress", "resolved"},
        }
        if row["version"] != expected_version or status not in transitions.get(
            row["status"], set()
        ):
            raise HTTPException(409, "Ticket changed or transition is not allowed")
        updated = (
            uow.execute(
                text("""UPDATE tickets SET status=:status, version=version+1,
            updated_at=now(), resolved_at=CASE WHEN :is_resolved THEN now() ELSE NULL END
            WHERE tenant_id=:tenant AND id=:id RETURNING *"""),
                {
                    "tenant": ctx.tenant_id,
                    "id": ticket_id,
                    "status": status,
                    "is_resolved": status == "resolved",
                },
            )
            .mappings()
            .one()
        )
        return TicketSummary.model_validate(
            {key: updated[key] for key in TicketSummary.model_fields}
        )

    def _customer_ticket(self, uow: Session, principal: CustomerPrincipal, ticket_id: UUID):
        initial = self._ticket(uow, principal.tenant_id, ticket_id)
        conversation_id = initial["conversation_id"]
        if (
            principal.conversation_scope is not None
            and principal.conversation_scope != conversation_id
        ):
            raise HTTPException(404, "Ticket unavailable")
        state = self.conversations.lock_for_handoff(uow, principal.tenant_id, conversation_id)
        if (state.tenant_id, state.customer_id, state.widget_id, state.id) != (
            principal.tenant_id,
            principal.customer_id,
            principal.widget_id,
            conversation_id,
        ):
            raise HTTPException(404, "Ticket unavailable")
        return self._ticket(uow, principal.tenant_id, ticket_id, lock=True), state

    def reopen(self, uow: Session, principal: CustomerPrincipal, ticket_id: UUID) -> TicketSummary:
        row, state = self._customer_ticket(uow, principal, ticket_id)
        if row["status"] != "resolved" or self.has_active(uow, principal.tenant_id, state.id):
            raise HTTPException(409, "Ticket cannot be reopened")
        updated = (
            uow.execute(
                text("""UPDATE tickets SET status='in_progress', version=version+1,
            updated_at=now(), resolved_at=NULL WHERE tenant_id=:tenant AND id=:id RETURNING *"""),
                {"tenant": principal.tenant_id, "id": ticket_id},
            )
            .mappings()
            .one()
        )
        self.conversations.mark_human(uow, state)
        return TicketSummary.model_validate(
            {key: updated[key] for key in TicketSummary.model_fields}
        )

    def confirm(self, uow: Session, principal: CustomerPrincipal, ticket_id: UUID) -> TicketSummary:
        row, state = self._customer_ticket(uow, principal, ticket_id)
        if row["status"] != "resolved":
            raise HTTPException(409, "Ticket cannot be confirmed")
        updated = (
            uow.execute(
                text("""UPDATE tickets SET status='closed', version=version+1,
            updated_at=now(), closed_at=now() WHERE tenant_id=:tenant AND id=:id RETURNING *"""),
                {"tenant": principal.tenant_id, "id": ticket_id},
            )
            .mappings()
            .one()
        )
        self.conversations.confirm_human(uow, principal.tenant_id, state.id)
        return TicketSummary.model_validate(
            {key: updated[key] for key in TicketSummary.model_fields}
        )
