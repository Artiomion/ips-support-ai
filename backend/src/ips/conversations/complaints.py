"""Customer complaints with immutable answer source snapshots."""

import json
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, ValidationError, WithJsonSchema
from sqlalchemy import text
from sqlalchemy.orm import Session

from ips.conversations.repository import ConversationRepository
from ips.core.db import get_session
from ips.core.ports import DTO, Citation, CustomerPrincipal, TenantContext
from ips.platform.auth import require_csrf, require_role
from ips.platform.members import cursor_value
from ips.platform.schemas import ErrorResponse
from ips.platform.widget_api import customer_principal

errors: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 429, 503)
}
router = APIRouter(prefix="/api/v1", tags=["C"], responses=errors)
admin = require_role("admin")
Cursor = Annotated[str | None, WithJsonSchema({"type": "string"})]


class ComplaintInput(DTO):
    message_id: UUID
    reason: str = Field(min_length=1, max_length=2000)


class ComplaintDecision(DTO):
    admin_note: str = Field(min_length=1, max_length=4000)


class Complaint(DTO):
    id: UUID
    message_id: UUID
    conversation_id: UUID
    reason: str
    status: Literal["pending", "reviewed"]
    source_snapshot: list[Citation]
    admin_note: str | None
    created_at: datetime


class ComplaintPage(DTO):
    items: list[Complaint]
    next_cursor: str | None


class Queued(DTO):
    state: Literal["queued"]


def _complaint(row):
    return Complaint.model_validate({key: row[key] for key in Complaint.model_fields})


@router.post(
    "/public/conversations/{conversation_id}/complaints",
    operation_id="reportAiComplaint",
    response_model=Queued,
    status_code=202,
)
def report(
    conversation_id: UUID,
    body: ComplaintInput,
    principal: Annotated[CustomerPrincipal, Depends(customer_principal)],
    db: Annotated[Session, Depends(get_session)],
):
    if ConversationRepository().find_customer(db, principal, conversation_id) is None:
        raise HTTPException(404, "Conversation unavailable")
    message = db.execute(
        text(
            "SELECT sources FROM messages WHERE tenant_id=:tenant AND conversation_id=:conversation AND id=:id AND kind='assistant'"
        ),
        {"tenant": principal.tenant_id, "conversation": conversation_id, "id": body.message_id},
    ).scalar_one_or_none()
    if message is None:
        raise HTTPException(404, "Message unavailable")
    try:
        snapshot = [Citation.model_validate(source).model_dump(mode="json") for source in message]
    except (ValidationError, TypeError):
        raise HTTPException(503, "Source snapshot unavailable") from None
    db.execute(
        text("""INSERT INTO ai_complaints (id,tenant_id,conversation_id,message_id,reason,source_snapshot)
      VALUES (:id,:tenant,:conversation,:message,:reason,CAST(:sources AS jsonb))"""),
        {
            "id": uuid4(),
            "tenant": principal.tenant_id,
            "conversation": conversation_id,
            "message": body.message_id,
            "reason": body.reason,
            "sources": json.dumps(snapshot),
        },
    )
    db.commit()
    return Queued(state="queued")


@router.get("/complaints", operation_id="listComplaints", response_model=ComplaintPage)
def list_complaints(
    ctx: Annotated[TenantContext, Depends(admin)],
    db: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: Cursor = None,
):
    rows = (
        db.execute(
            text(
                "SELECT * FROM ai_complaints WHERE tenant_id=:tenant AND id>:after ORDER BY id LIMIT :limit"
            ),
            {"tenant": ctx.tenant_id, "after": cursor_value(cursor), "limit": limit + 1},
        )
        .mappings()
        .all()
    )
    return ComplaintPage(
        items=[_complaint(row) for row in rows[:limit]],
        next_cursor=str(rows[limit - 1]["id"]) if len(rows) > limit else None,
    )


@router.get("/complaints/{complaint_id}", operation_id="getComplaint", response_model=Complaint)
def get_complaint(
    complaint_id: UUID,
    ctx: Annotated[TenantContext, Depends(admin)],
    db: Annotated[Session, Depends(get_session)],
):
    row = (
        db.execute(
            text("SELECT * FROM ai_complaints WHERE tenant_id=:tenant AND id=:id"),
            {"tenant": ctx.tenant_id, "id": complaint_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, "Complaint unavailable")
    return _complaint(row)


@router.post(
    "/complaints/{complaint_id}/review",
    operation_id="reviewComplaint",
    response_model=Complaint,
    dependencies=[Depends(require_csrf)],
)
def review(
    complaint_id: UUID,
    body: ComplaintDecision,
    ctx: Annotated[TenantContext, Depends(admin)],
    db: Annotated[Session, Depends(get_session)],
):
    row = (
        db.execute(
            text("""UPDATE ai_complaints SET status='reviewed',admin_note=:note
      WHERE tenant_id=:tenant AND id=:id AND status='pending' RETURNING *"""),
            {"tenant": ctx.tenant_id, "id": complaint_id, "note": body.admin_note},
        )
        .mappings()
        .first()
    )
    if row is None:
        existing = db.execute(
            text("SELECT 1 FROM ai_complaints WHERE tenant_id=:tenant AND id=:id"),
            {"tenant": ctx.tenant_id, "id": complaint_id},
        ).first()
        raise HTTPException(409 if existing else 404, "Complaint unavailable")
    result = _complaint(row)
    db.commit()
    return result
