"""Customer-facing ticket routes. Conversation ownership is checked by TicketService."""

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from ips.core.db import get_session
from ips.core.ports import CustomerPrincipal, TenantContext, TicketSummary
from ips.platform.audit import record
from ips.platform.auth import require_csrf, tenant_context
from ips.platform.members import cursor_value
from ips.platform.schemas import ErrorResponse
from ips.platform.widget_api import customer_principal
from ips.tickets.assignment import claim_ticket, reassign_ticket
from ips.tickets.service import TicketService

errors: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 429, 503)
}
router = APIRouter(prefix="/api/v1/public", tags=["B"], responses=errors)
staff_router = APIRouter(prefix="/api/v1", tags=["B"], responses=errors)


class HandoffInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=1000)


class TicketResponse(TicketSummary):
    version: int = Field(ge=1)


class ConversationTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket: TicketResponse | None


class TicketPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[TicketResponse]
    next_cursor: str | None


class TicketUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    status: Annotated[
        Literal["in_progress", "waiting_customer", "resolved"] | None,
        WithJsonSchema({"type": "string", "enum": ["in_progress", "waiting_customer", "resolved"]}),
    ] = None
    priority: Annotated[
        Literal["low", "normal", "high", "urgent"] | None,
        WithJsonSchema({"type": "string", "enum": ["low", "normal", "high", "urgent"]}),
    ] = None
    category_id: UUID | None = None
    assignee_id: UUID | None = None

    @field_validator("status", "priority", mode="before")
    @classmethod
    def reject_null(cls, value):
        if value is None:
            raise ValueError("Explicit null is not supported")
        return value


class VersionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


@staff_router.post(
    "/tickets/{ticket_id}/claim",
    operation_id="claimTicket",
    response_model=TicketResponse,
    dependencies=[Depends(require_csrf)],
)
def claim(
    ticket_id: UUID,
    body: VersionInput,
    ctx: Annotated[TenantContext, Depends(tenant_context)],
    db: Annotated[Session, Depends(get_session)],
) -> TicketSummary:
    result = claim_ticket(db, ctx, ticket_id, body.expected_version)
    record(db, ctx.tenant_id, ctx.user_id, "ticket.claim", ticket_id)
    db.commit()
    return result


@staff_router.put(
    "/tickets/{ticket_id}",
    operation_id="updateTicket",
    response_model=TicketResponse,
    dependencies=[Depends(require_csrf)],
)
def update_ticket(
    ticket_id: UUID,
    body: TicketUpdate,
    ctx: Annotated[TenantContext, Depends(tenant_context)],
    db: Annotated[Session, Depends(get_session)],
) -> TicketSummary:
    if "assignee_id" in body.model_fields_set:
        changes = body.model_dump(exclude_unset=True, exclude={"expected_version", "assignee_id"})
        result = reassign_ticket(
            db, ctx, ticket_id, body.expected_version, body.assignee_id, changes
        )
    else:
        changes = body.model_dump(exclude_unset=True, exclude={"expected_version"})
        result = TicketService.update_details(db, ctx, ticket_id, body.expected_version, changes)
    record(db, ctx.tenant_id, ctx.user_id, "ticket.update", ticket_id)
    db.commit()
    return result


@staff_router.get("/tickets", operation_id="listTickets", response_model=TicketPage)
def list_tickets(
    ctx: Annotated[TenantContext, Depends(tenant_context)],
    db: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: Annotated[str | None, WithJsonSchema({"type": "string"})] = None,
    status: Annotated[str | None, WithJsonSchema({"type": "string"})] = None,
    q: Annotated[str | None, WithJsonSchema({"type": "string"})] = None,
    widget_id: Annotated[UUID | None, WithJsonSchema({"type": "string", "format": "uuid"})] = None,
) -> TicketPage:
    clauses = ["t.tenant_id=:tenant", "t.id>:after"]
    params = {"tenant": ctx.tenant_id, "after": cursor_value(cursor), "limit": limit + 1}
    if status:
        clauses.append("t.status=:status")
        params["status"] = status
    if widget_id is not None:
        clauses.append("""EXISTS (
            SELECT 1 FROM conversations c
            WHERE c.tenant_id=t.tenant_id AND c.id=t.conversation_id AND c.widget_id=:widget
        )""")
        params["widget"] = widget_id
    query = q.strip() if q is not None else ""
    if "\x00" in query:
        raise HTTPException(422, "Invalid search query")
    if query:
        clauses.append("""EXISTS (
            SELECT 1 FROM messages m
            WHERE m.tenant_id=t.tenant_id AND m.conversation_id=t.conversation_id
              AND m.kind IN ('customer', 'assistant', 'specialist')
              AND strpos(lower(m.text), lower(:query)) > 0
        )""")
        params["query"] = query
    rows = (
        db.execute(
            text(
                "SELECT t.* FROM tickets t WHERE "
                + " AND ".join(clauses)
                + " ORDER BY t.id LIMIT :limit"
            ),
            params,
        )
        .mappings()
        .all()
    )
    items = [
        TicketResponse.model_validate({key: row[key] for key in TicketSummary.model_fields})
        for row in rows[:limit]
    ]
    return TicketPage(
        items=items, next_cursor=str(rows[limit - 1]["id"]) if len(rows) > limit else None
    )


@staff_router.get("/tickets/{ticket_id}", operation_id="getTicket", response_model=TicketResponse)
def get_staff_ticket(
    ticket_id: UUID,
    ctx: Annotated[TenantContext, Depends(tenant_context)],
    db: Annotated[Session, Depends(get_session)],
) -> TicketSummary:
    row = (
        db.execute(
            text("SELECT * FROM tickets WHERE tenant_id=:tenant AND id=:id"),
            {"tenant": ctx.tenant_id, "id": ticket_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, "Ticket unavailable")
    return TicketSummary.model_validate({key: row[key] for key in TicketSummary.model_fields})


def service_for(request: Request) -> TicketService:
    # The conversation port is required to verify customer ownership.
    port = getattr(request.app.state.ports, "conversations", None)
    if port is None:
        raise HTTPException(503, "Conversation service unavailable")
    return TicketService(port)


@router.post(
    "/conversations/{conversation_id}/handoff",
    operation_id="requestHandoff",
    response_model=TicketResponse,
)
def request_handoff(
    conversation_id: UUID,
    body: HandoffInput,
    request: Request,
    principal: Annotated[CustomerPrincipal, Depends(customer_principal)],
    db: Annotated[Session, Depends(get_session)],
) -> TicketSummary:
    result = service_for(request).escalate(
        db, principal, conversation_id, body.reason, None, "normal"
    )
    db.commit()
    return result


@router.get("/tickets/{ticket_id}", operation_id="getCustomerTicket", response_model=TicketResponse)
def get_customer_ticket(
    ticket_id: UUID,
    request: Request,
    principal: Annotated[CustomerPrincipal, Depends(customer_principal)],
    db: Annotated[Session, Depends(get_session)],
) -> TicketSummary:
    row, _ = service_for(request)._customer_ticket(db, principal, ticket_id)
    return TicketSummary.model_validate({key: row[key] for key in TicketSummary.model_fields})


@router.get("/conversations/{conversation_id}/ticket", operation_id="getConversationTicket")
def get_conversation_ticket(
    conversation_id: UUID,
    response: Response,
    principal: Annotated[CustomerPrincipal, Depends(customer_principal)],
    db: Annotated[Session, Depends(get_session)],
) -> ConversationTicket:
    if principal.conversation_scope not in (None, conversation_id):
        raise HTTPException(404, "Conversation unavailable")
    row = (
        db.execute(
            text("""SELECT ticket.* FROM conversations c
            LEFT JOIN LATERAL (
                SELECT t.* FROM tickets t
                WHERE t.tenant_id=c.tenant_id AND t.conversation_id=c.id
                ORDER BY (t.status IN ('resolved', 'closed')),
                    coalesce(t.closed_at,t.resolved_at,t.created_at) DESC,
                    t.created_at DESC, t.id DESC
                LIMIT 1
            ) ticket ON true
            WHERE c.id=:id AND c.tenant_id=:tenant
                AND c.customer_id=:customer AND c.widget_id=:widget"""),
            {
                "id": conversation_id,
                "tenant": principal.tenant_id,
                "customer": principal.customer_id,
                "widget": principal.widget_id,
            },
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, "Conversation unavailable")
    response.headers["Cache-Control"] = "no-store"
    ticket = (
        TicketResponse.model_validate({key: row[key] for key in TicketResponse.model_fields})
        if row["id"] is not None
        else None
    )
    return ConversationTicket(ticket=ticket)


@router.post(
    "/tickets/{ticket_id}/reopen", operation_id="reopenTicket", response_model=TicketResponse
)
def reopen_ticket(
    ticket_id: UUID,
    request: Request,
    principal: Annotated[CustomerPrincipal, Depends(customer_principal)],
    db: Annotated[Session, Depends(get_session)],
) -> TicketSummary:
    result = service_for(request).reopen(db, principal, ticket_id)
    db.commit()
    return result


@router.post(
    "/tickets/{ticket_id}/confirm", operation_id="confirmTicket", response_model=TicketResponse
)
def confirm_ticket(
    ticket_id: UUID,
    request: Request,
    principal: Annotated[CustomerPrincipal, Depends(customer_principal)],
    db: Annotated[Session, Depends(get_session)],
) -> TicketSummary:
    result = service_for(request).confirm(db, principal, ticket_id)
    db.commit()
    return result
