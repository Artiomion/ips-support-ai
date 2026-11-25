"""Tenant-scoped overview of conversations and ticket response times."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, WithJsonSchema
from sqlalchemy import text
from sqlalchemy.orm import Session

from ips.core.db import get_session
from ips.core.ports import TenantContext
from ips.platform.auth import require_role
from ips.platform.widget_api import Strict, errors

router = APIRouter(prefix="/api/v1/analytics", tags=["B"], responses=errors)
admin = require_role("admin")


class Overview(Strict):
    conversations: int = Field(ge=0)
    tickets: int = Field(ge=0)
    ai_confirmed: int = Field(ge=0)
    unconfirmed: int = Field(ge=0)
    handoffs: int = Field(ge=0)
    first_response_seconds_avg: float | None
    resolution_seconds_avg: float | None


@router.get("/overview", operation_id="getOverview")
def get_overview(
    request: Request,
    ctx: Annotated[TenantContext, Depends(admin)],
    db: Annotated[Session, Depends(get_session)],
    widget_id: Annotated[UUID | None, WithJsonSchema({"type": "string", "format": "uuid"})] = None,
) -> Overview:
    params = {"tenant": ctx.tenant_id, "widget": widget_id}
    if (
        widget_id is not None
        and db.execute(
            text("SELECT id FROM widgets WHERE id=:widget AND tenant_id=:tenant"), params
        ).first()
        is None
    ):
        raise HTTPException(404, "Widget unavailable")
    service = getattr(request.app.state.ports, "conversations", None)
    if service is None:
        raise HTTPException(503, "Conversation service unavailable")
    counts = service.metrics(db, ctx, widget_id)
    tickets = (
        db.execute(
            text("""SELECT count(*) AS tickets,
            avg(extract(epoch FROM t.first_staff_reply_at - t.created_at)) AS first_response_seconds_avg,
            avg(extract(epoch FROM t.resolved_at - t.created_at)) AS resolution_seconds_avg
        FROM tickets t
        JOIN conversations c ON c.tenant_id=t.tenant_id AND c.id=t.conversation_id
        WHERE t.tenant_id=:tenant AND (CAST(:widget AS uuid) IS NULL OR c.widget_id=:widget)"""),
            params,
        )
        .mappings()
        .one()
    )
    return Overview(**counts.model_dump(), **tickets)
