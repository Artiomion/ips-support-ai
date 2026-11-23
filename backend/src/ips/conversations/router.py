"""Conversation HTTP routes; composition root registers these routers."""

from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import Field, WithJsonSchema
from sqlalchemy.orm import Session

from ips.conversations.events import StaffReplyService
from ips.conversations.repository import ConversationRepository
from ips.conversations.schemas import (
    Conversation,
    CreateConversation,
    MessageInput,
    PublicMessage,
    StaffMessageInput,
)
from ips.conversations.service import ConversationService
from ips.core.db import get_session
from ips.core.ports import DTO, CustomerPrincipal, StaffMessage, TenantContext
from ips.platform.auth import require_csrf, tenant_context
from ips.platform.schemas import ErrorResponse
from ips.platform.widget_api import customer_principal

errors: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 429, 503)
}
public_router = APIRouter(prefix="/api/v1/public/conversations", tags=["C"], responses=errors)
staff_router = APIRouter(prefix="/api/v1/conversations", tags=["C"], responses=errors)


class ConversationPage(DTO):
    items: list[Conversation]
    next_cursor: str | None


class PublicMessagePage(DTO):
    items: list[PublicMessage]
    next_cursor: str | None


class StaffMessageResponse(StaffMessage):
    seq: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=8000)


class StaffMessagePage(DTO):
    items: list[StaffMessageResponse]
    next_cursor: str | None


def _service():
    return ConversationService(ConversationRepository())


def _conversation(row):
    return Conversation.model_validate({key: row[key] for key in Conversation.model_fields})


def _after(cursor, after_seq):
    if cursor is None:
        return after_seq or 0
    try:
        value = int(cursor)
        if value < 0 or str(value) != cursor:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(422, "Invalid cursor") from exc
    if after_seq is not None and after_seq != value:
        raise HTTPException(422, "Conflicting cursor")
    return value


@public_router.post(
    "", operation_id="createConversation", status_code=201, response_model=Conversation
)
def create_conversation(
    body: CreateConversation,
    principal: Annotated[CustomerPrincipal, Depends(customer_principal)],
    db: Annotated[Session, Depends(get_session)],
):
    if principal.conversation_scope is not None:
        raise HTTPException(403, "Scoped session cannot create a conversation")
    service = _service()
    row = service.repository.create(db, principal)
    if body.initial_text is not None:
        service.send_customer(db, principal, row["id"], uuid4(), body.initial_text)
    db.commit()
    return _conversation(row)


@public_router.get("", operation_id="listCustomerConversations", response_model=ConversationPage)
def list_conversations(
    principal: Annotated[CustomerPrincipal, Depends(customer_principal)],
    db: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: Annotated[str | None, WithJsonSchema({"type": "string"})] = None,
):
    try:
        after = UUID(cursor) if cursor else UUID(int=0)
    except ValueError as exc:
        raise HTTPException(422, "Invalid cursor") from exc
    rows = _service().repository.list_customer(db, principal, after, limit + 1)
    return ConversationPage(
        items=[_conversation(row) for row in rows[:limit]],
        next_cursor=str(rows[limit - 1]["id"]) if len(rows) > limit else None,
    )


@public_router.get(
    "/{conversation_id}/messages",
    operation_id="listCustomerMessages",
    response_model=PublicMessagePage,
)
def list_customer_messages(
    conversation_id: UUID,
    principal: Annotated[CustomerPrincipal, Depends(customer_principal)],
    db: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: Annotated[str | None, WithJsonSchema({"type": "string"})] = None,
    after_seq: Annotated[
        int | None, Query(ge=0), WithJsonSchema({"type": "integer", "minimum": 0})
    ] = None,
):
    rows = _service().customer_messages(
        db, principal, conversation_id, _after(cursor, after_seq), limit + 1
    )
    return PublicMessagePage(
        items=rows[:limit], next_cursor=str(rows[limit - 1].seq) if len(rows) > limit else None
    )


@public_router.post(
    "/{conversation_id}/messages",
    operation_id="sendCustomerMessage",
    status_code=201,
    response_model=PublicMessage,
    responses={200: {"model": PublicMessage}},
)
def send_customer_message(
    conversation_id: UUID,
    body: MessageInput,
    response: Response,
    request: Request,
    principal: Annotated[CustomerPrincipal, Depends(customer_principal)],
    db: Annotated[Session, Depends(get_session)],
):
    if body.attachment_ids:
        raise HTTPException(422, "Attachments are not available")
    result, created = _service().send_customer(
        db, principal, conversation_id, body.client_message_id, body.text
    )
    if created:
        tickets = getattr(request.app.state.ports, "tickets", None)
        if tickets is not None:
            tickets.on_customer_message(db, principal, conversation_id)
    else:
        response.status_code = 200
    db.commit()
    return result


@staff_router.get(
    "/{conversation_id}/messages", operation_id="listStaffMessages", response_model=StaffMessagePage
)
def list_staff_messages(
    conversation_id: UUID,
    ctx: Annotated[TenantContext, Depends(tenant_context)],
    db: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: Annotated[str | None, WithJsonSchema({"type": "string"})] = None,
    after_seq: Annotated[
        int | None, Query(ge=0), WithJsonSchema({"type": "integer", "minimum": 0})
    ] = None,
):
    rows = _service().staff_messages(db, ctx, conversation_id, _after(cursor, after_seq), limit + 1)
    return StaffMessagePage(
        items=[StaffMessageResponse.model_validate(row.model_dump()) for row in rows[:limit]],
        next_cursor=str(rows[limit - 1].seq) if len(rows) > limit else None,
    )


@staff_router.post(
    "/{conversation_id}/messages",
    operation_id="sendStaffMessage",
    status_code=201,
    response_model=StaffMessageResponse,
    responses={200: {"model": StaffMessageResponse}},
    dependencies=[Depends(require_csrf)],
)
def send_staff_message(
    conversation_id: UUID,
    body: StaffMessageInput,
    response: Response,
    request: Request,
    ctx: Annotated[TenantContext, Depends(tenant_context)],
    db: Annotated[Session, Depends(get_session)],
):
    ports = request.app.state.ports
    tickets, notifications = getattr(ports, "tickets", None), getattr(ports, "notifications", None)
    if not body.internal and (tickets is None or notifications is None):
        raise HTTPException(503, "Reply services unavailable")
    result, created = StaffReplyService(_service(), tickets, notifications).send(
        db, ctx, conversation_id, body.text, body.internal, body.client_message_id
    )
    if not created:
        response.status_code = 200
    db.commit()
    return StaffMessageResponse.model_validate(result.model_dump())
