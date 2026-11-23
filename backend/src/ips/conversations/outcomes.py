"""Customer outcomes, resuming the bot, and tenant-scoped metrics."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from ips.conversations.schemas import Conversation
from ips.conversations.service import ConversationService
from ips.core.db import get_session
from ips.core.ports import ConversationCounts, CustomerPrincipal
from ips.platform.schemas import ErrorResponse
from ips.platform.widget_api import customer_principal

errors: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 429, 503)
}
router = APIRouter(prefix="/api/v1/public/conversations", tags=["C"], responses=errors)


class OutcomeConversationService(ConversationService):
    def __init__(self, repository, tickets=None):
        super().__init__(repository)
        self.tickets = tickets

    def confirm_ai(self, db, principal, conversation_id):
        row = self._customer_conversation(db, principal, conversation_id, lock=True)
        if row["mode"] != "bot":
            raise HTTPException(409, "Human support is active")
        answered = db.execute(
            text("""SELECT EXISTS(SELECT 1 FROM messages
            WHERE tenant_id=:tenant AND conversation_id=:id AND kind='assistant')"""),
            {"tenant": principal.tenant_id, "id": conversation_id},
        ).scalar_one()
        if not answered:
            raise HTTPException(409, "No AI answer to confirm")
        return (
            db.execute(
                text("""UPDATE conversations SET outcome='ai_confirmed',updated_at=now()
            WHERE tenant_id=:tenant AND id=:id RETURNING *"""),
                {"tenant": principal.tenant_id, "id": conversation_id},
            )
            .mappings()
            .one()
        )

    def resume_bot(self, db, principal, conversation_id):
        row = self._customer_conversation(db, principal, conversation_id, lock=True)
        if self.tickets is None:
            raise HTTPException(503, "Ticket service unavailable")
        if self.tickets.has_active(db, principal.tenant_id, conversation_id):
            raise HTTPException(409, "Active ticket prevents bot resumption")
        if row["mode"] == "bot":
            return row
        return (
            db.execute(
                text("""UPDATE conversations SET mode='bot',version=version+1,updated_at=now()
            WHERE tenant_id=:tenant AND id=:id RETURNING *"""),
                {"tenant": principal.tenant_id, "id": conversation_id},
            )
            .mappings()
            .one()
        )

    def confirm_human(self, uow, tenant_id, conversation_id):
        row = self.repository.find_staff(uow, tenant_id, conversation_id, lock=True)
        if row is None:
            raise HTTPException(404, "Conversation unavailable")
        uow.execute(
            text("""UPDATE conversations SET outcome='human_confirmed',updated_at=now()
            WHERE tenant_id=:tenant AND id=:id"""),
            {"tenant": tenant_id, "id": conversation_id},
        )

    def metrics(self, uow, ctx, widget_id):
        row = (
            uow.execute(
                text("""SELECT count(*) AS conversations,
            count(*) FILTER (WHERE c.outcome='ai_confirmed') AS ai_confirmed,
            count(*) FILTER (WHERE c.outcome='unconfirmed') AS unconfirmed,
            count(*) FILTER (WHERE EXISTS (SELECT 1 FROM tickets t
                WHERE t.tenant_id=c.tenant_id AND t.conversation_id=c.id)) AS handoffs
            FROM conversations c WHERE c.tenant_id=:tenant
              AND (CAST(:widget AS uuid) IS NULL OR c.widget_id=:widget)"""),
                {"tenant": ctx.tenant_id, "widget": widget_id},
            )
            .mappings()
            .one()
        )
        return ConversationCounts.model_validate(dict(row))


def _conversation(row):
    return Conversation.model_validate({key: row[key] for key in Conversation.model_fields})


@router.post(
    "/{conversation_id}/confirm-ai", operation_id="confirmAiResolution", response_model=Conversation
)
def confirm_ai(
    conversation_id: UUID,
    request: Request,
    principal: Annotated[CustomerPrincipal, Depends(customer_principal)],
    db: Annotated[Session, Depends(get_session)],
):
    row = request.app.state.ports.conversations.confirm_ai(db, principal, conversation_id)
    result = _conversation(row)
    db.commit()
    return result


@router.post("/{conversation_id}/resume-bot", operation_id="resumeBot", response_model=Conversation)
def resume_bot(
    conversation_id: UUID,
    request: Request,
    principal: Annotated[CustomerPrincipal, Depends(customer_principal)],
    db: Annotated[Session, Depends(get_session)],
):
    row = request.app.state.ports.conversations.resume_bot(db, principal, conversation_id)
    result = _conversation(row)
    db.commit()
    return result
