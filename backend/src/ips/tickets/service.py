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
