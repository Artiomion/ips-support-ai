"""Record a rating from the customer who owns the resolved ticket."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, WithJsonSchema, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from ips.core.db import get_session
from ips.core.ports import CustomerPrincipal
from ips.platform.widget_api import Strict, customer_principal, errors
from ips.tickets.router import service_for

router = APIRouter(prefix="/api/v1/public/tickets", tags=["B"], responses=errors)


class RatingInput(Strict):
    score: int = Field(strict=True, ge=1, le=5)
    comment: Annotated[str | None, WithJsonSchema({"type": "string", "maxLength": 2000})] = Field(
        default=None, max_length=2000
    )

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str | None) -> str:
        if value is None or "\x00" in value:
            raise ValueError("Invalid comment")
        return value


@router.post("/{ticket_id}/rating", operation_id="rateTicket", response_model_exclude_none=True)
def rate_ticket(
    ticket_id: UUID,
    body: RatingInput,
    request: Request,
    principal: Annotated[CustomerPrincipal, Depends(customer_principal)],
    db: Annotated[Session, Depends(get_session)],
) -> RatingInput:
    ticket, _ = service_for(request)._customer_ticket(db, principal, ticket_id)
    if ticket["status"] not in ("resolved", "closed"):
        raise HTTPException(409, "Ticket must be resolved before rating")
    row = db.execute(
        text("""INSERT INTO ratings (tenant_id, ticket_id, customer_id, score, comment)
        VALUES (:tenant,:ticket,:customer,:score,:comment)
        ON CONFLICT (ticket_id) DO NOTHING RETURNING ticket_id"""),
        {
            "tenant": principal.tenant_id,
            "ticket": ticket_id,
            "customer": principal.customer_id,
            "score": body.score,
            "comment": body.comment,
        },
    ).first()
    if row is None:
        raise HTTPException(409, "Ticket already rated")
    db.commit()
    return body
