"""Ticket domain operations. All changes use the caller's transaction."""

from datetime import datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from ips.core.ports import ConversationPort, CustomerPrincipal, TenantContext, TicketSummary
from ips.tickets.routing import route_ticket


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
        # Capacity paths lock memberships before conversations and tickets.
        uow.execute(
            text("SELECT id FROM memberships WHERE tenant_id=:tenant ORDER BY id FOR UPDATE"),
            {"tenant": principal.tenant_id},
        ).all()
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
        created = row is None
        if created:
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
        if created:
            assigned = route_ticket(uow, principal.tenant_id, row["id"], row["version"])
            if assigned is not None:
                return assigned
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

    @staticmethod
    def _ticket(uow: Session, tenant_id: UUID, ticket_id: UUID, *, lock: bool = False):
        query = "SELECT * FROM tickets WHERE tenant_id=:tenant AND id=:id"
        if lock:
            query += " FOR UPDATE"
        row = uow.execute(text(query), {"tenant": tenant_id, "id": ticket_id}).mappings().first()
        if row is None:
            raise HTTPException(404, "Ticket unavailable")
        return row

    @staticmethod
    def update_status(
        uow: Session, ctx: TenantContext, ticket_id: UUID, expected_version: int, status: str
    ) -> TicketSummary:
        return TicketService.update_details(
            uow, ctx, ticket_id, expected_version, {"status": status}
        )

    @staticmethod
    def update_details(
        uow: Session, ctx: TenantContext, ticket_id: UUID, expected_version: int, changes: dict
    ) -> TicketSummary:
        # Keep the version check and all requested changes under the same row lock.
        row = TicketService._ticket(uow, ctx.tenant_id, ticket_id, lock=True)
        if ctx.role != "admin" and row["assignee_id"] != ctx.membership_id:
            raise HTTPException(403, "Ticket is assigned to another specialist")
        if row["version"] != expected_version:
            raise HTTPException(409, "Ticket changed")
        if not changes or set(changes) - {"status", "priority", "category_id"}:
            raise HTTPException(422, "Invalid update")
        transitions = {
            "in_progress": {"waiting_customer", "resolved"},
            "waiting_customer": {"in_progress", "resolved"},
        }
        status = changes.get("status")
        if "status" in changes and status is None:
            raise HTTPException(422, "Invalid status")
        if status is not None and status not in transitions.get(row["status"], set()):
            raise HTTPException(409, "Ticket changed or transition is not allowed")
        if "priority" in changes and changes["priority"] not in ("low", "normal", "high", "urgent"):
            raise HTTPException(422, "Invalid priority")
        category_id = changes.get("category_id")
        if "category_id" in changes and category_id is not None:
            category = uow.execute(
                text("SELECT id FROM categories WHERE tenant_id=:tenant AND id=:id FOR KEY SHARE"),
                {"tenant": ctx.tenant_id, "id": category_id},
            ).scalar_one_or_none()
            if category is None:
                raise HTTPException(404, "Category unavailable")
        fields = ["version=version+1", "updated_at=now()"]
        params = {"tenant": ctx.tenant_id, "id": ticket_id}
        for key in ("status", "priority", "category_id"):
            if key in changes:
                fields.append(f"{key}=:{key}")
                params[key] = changes[key]
        if status is not None:
            fields.append("resolved_at=CASE WHEN :is_resolved THEN now() ELSE NULL END")
            params["is_resolved"] = status == "resolved"
        updated = (
            uow.execute(
                text(
                    "UPDATE tickets SET "
                    + ", ".join(fields)
                    + " WHERE tenant_id=:tenant AND id=:id RETURNING *"
                ),
                params,
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
        initial = self._ticket(uow, principal.tenant_id, ticket_id)
        if (
            principal.conversation_scope is not None
            and principal.conversation_scope != initial["conversation_id"]
        ):
            raise HTTPException(404, "Ticket unavailable")
        assignee_id = initial["assignee_id"]
        member = None
        if assignee_id is not None:
            # Capacity-changing paths always lock membership before the ticket.
            member = (
                uow.execute(
                    text(
                        "SELECT max_active FROM memberships WHERE tenant_id=:tenant AND id=:id FOR UPDATE"
                    ),
                    {"tenant": principal.tenant_id, "id": assignee_id},
                )
                .mappings()
                .first()
            )
        row, state = self._customer_ticket(uow, principal, ticket_id)
        if (
            row["status"] != "resolved"
            or row["assignee_id"] != assignee_id
            or self.has_active(uow, principal.tenant_id, state.id)
        ):
            raise HTTPException(409, "Ticket cannot be reopened")
        if assignee_id is not None:
            if member is None:
                raise HTTPException(409, "Assignee unavailable")
            active = uow.execute(
                text("""SELECT count(*) FROM tickets WHERE tenant_id=:tenant AND assignee_id=:id
                    AND status NOT IN ('resolved','closed')"""),
                {"tenant": principal.tenant_id, "id": assignee_id},
            ).scalar_one()
            if active >= member["max_active"]:
                raise HTTPException(409, "Assignee at capacity")
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
