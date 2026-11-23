"""Audit metadata only: never accept request bodies, secrets or email addresses."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, WithJsonSchema
from sqlalchemy import text
from sqlalchemy.orm import Session

from ips.core.db import get_session
from ips.core.ports import TenantContext
from ips.platform.auth import require_role
from ips.platform.schemas import ErrorResponse

errors: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 429, 503)
}
router = APIRouter(prefix="/api/v1", tags=["B"], responses=errors)
admin = require_role("admin")


class AuditEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    action: str
    object_id: UUID | None
    created_at: datetime


class AuditEventPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[AuditEvent]
    next_cursor: str | None


def record(db: Session, tenant_id: UUID, actor_id: UUID, action: str, object_id: UUID | None):
    allowed = {
        "organization.update",
        "widget.create",
        "widget.update",
        "member.invite",
        "member.accept",
        "member.update",
        "category.create",
        "ticket.claim",
        "ticket.update",
    }
    if action not in allowed:
        raise ValueError("Unsupported audit action")
    db.execute(
        text(
            "INSERT INTO audit_events (id,tenant_id,actor_id,action,object_id) VALUES (:id,:tenant,:actor,:action,:object)"
        ),
        {
            "id": uuid4(),
            "tenant": tenant_id,
            "actor": actor_id,
            "action": action,
            "object": object_id,
        },
    )


@router.get("/audit", operation_id="listAuditEvents", response_model=AuditEventPage)
def list_audit_events(
    ctx: Annotated[TenantContext, Depends(admin)],
    db: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: Annotated[str | None, WithJsonSchema({"type": "string"})] = None,
):
    try:
        after = UUID(cursor) if cursor else UUID(int=0)
    except ValueError as exc:
        raise HTTPException(422, "Invalid cursor") from exc
    rows = (
        db.execute(
            text(
                "SELECT id,action,object_id,created_at FROM audit_events WHERE tenant_id=:tenant AND id>:after ORDER BY id LIMIT :limit"
            ),
            {"tenant": ctx.tenant_id, "after": after, "limit": limit + 1},
        )
        .mappings()
        .all()
    )
    return AuditEventPage(
        items=[AuditEvent(**row) for row in rows[:limit]],
        next_cursor=str(rows[limit - 1]["id"]) if len(rows) > limit else None,
    )
