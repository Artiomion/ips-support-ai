"""Create and consume one-time grants for a single conversation."""

import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, Security
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import EmailStr, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from ips.core.db import get_session
from ips.core.jobs import enqueue
from ips.core.ports import CustomerPrincipal
from ips.platform.widget_api import Strict, bearer, customer_principal, errors, token_hash

router = APIRouter(prefix="/api/v1/public/customer", tags=["B"], responses=errors)


class EmailInput(Strict):
    conversation_id: UUID
    email: EmailStr


class TokenInput(Strict):
    token: str = Field(min_length=20, max_length=2048)


class Queued(Strict):
    state: Literal["queued"]


class CustomerToken(Strict):
    access_token: str
    expires_at: datetime
    conversation_id: UUID


def link_token(secret: str, purpose: Literal["confirm", "exchange"], link_id: UUID) -> str:
    # Keep plaintext out of both tables while allowing the worker to retry delivery.
    return hmac.digest(
        secret.encode(), f"ips:customer-link:v1:{purpose}:".encode() + link_id.bytes, "sha256"
    ).hex()


@router.post("/email/request", operation_id="requestCustomerEmail", status_code=202)
def request_email(
    body: EmailInput,
    request: Request,
    principal: Annotated[CustomerPrincipal, Depends(customer_principal)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)],
    db: Annotated[Session, Depends(get_session)],
) -> Queued:
    if credentials is None:
        raise HTTPException(401, "Unauthorized")
    if principal.conversation_scope not in (None, body.conversation_id):
        raise HTTPException(404, "Conversation unavailable")
    service = getattr(request.app.state.ports, "conversations", None)
    if service is None:
        raise HTTPException(503, "Conversation service unavailable")
    state = service.lock_for_handoff(db, principal.tenant_id, body.conversation_id)
    if (state.tenant_id, state.id, state.customer_id, state.widget_id) != (
        principal.tenant_id,
        body.conversation_id,
        principal.customer_id,
        principal.widget_id,
    ):
        raise HTTPException(404, "Conversation unavailable")
    link_id = uuid4()
    token = link_token(request.app.state.settings.session_secret, "confirm", link_id)
    row = db.execute(
        text("""INSERT INTO customer_links
            (id, tenant_id, customer_id, conversation_id, purpose, site_origin, email, token_hash)
        SELECT :id, s.tenant_id, s.customer_id, :conversation, 'confirm', s.site_origin, :email, :hash
        FROM customer_sessions s JOIN widgets w ON w.tenant_id=s.tenant_id AND w.id=s.widget_id
        WHERE s.token_hash=:session AND s.tenant_id=:tenant AND s.customer_id=:customer
          AND s.widget_id=:widget AND s.expires_at > now()
          AND w.enabled AND s.site_origin=ANY(w.allowed_origins)
        RETURNING id"""),
        {
            "id": link_id,
            "tenant": principal.tenant_id,
            "customer": principal.customer_id,
            "widget": principal.widget_id,
            "conversation": body.conversation_id,
            "email": body.email,
            "hash": token_hash(token),
            "session": token_hash(credentials.credentials),
        },
    ).first()
    if row is None:
        raise HTTPException(401, "Unauthorized")
    enqueue(
        db.connection(),
        principal.tenant_id,
        "customer_email",
        {"tenant_id": str(principal.tenant_id), "link_id": str(link_id)},
        f"customer_email:{link_id}",
    )
    db.commit()
    return Queued(state="queued")


def consume_link(
    db: Session, response: Response, token: str, purpose: Literal["confirm", "exchange"]
) -> CustomerToken:
    row = db.execute(
        text("""UPDATE customer_links l SET consumed_at=clock_timestamp()
        FROM conversations c JOIN widgets w ON w.tenant_id=c.tenant_id AND w.id=c.widget_id
        WHERE l.token_hash=:hash AND l.purpose=:purpose AND l.consumed_at IS NULL
          AND l.expires_at > clock_timestamp() AND c.tenant_id=l.tenant_id
          AND c.id=l.conversation_id AND c.customer_id=l.customer_id
          AND w.enabled AND l.site_origin=ANY(w.allowed_origins)
          AND (l.purpose='confirm' OR l.confirmation_id=(
              SELECT v.id FROM customer_links v
              WHERE v.tenant_id=l.tenant_id AND v.conversation_id=l.conversation_id
                AND v.purpose='confirm' AND v.consumed_at IS NOT NULL
              ORDER BY v.consumed_at DESC, v.id DESC LIMIT 1
          ))
        RETURNING l.tenant_id, l.customer_id, l.conversation_id, l.site_origin, c.widget_id"""),
        {"hash": token_hash(token), "purpose": purpose},
    ).first()
    if row is None:
        raise HTTPException(404, "Link unavailable")
    access_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(days=90)
    db.execute(
        text("""INSERT INTO customer_sessions
            (id, tenant_id, customer_id, widget_id, token_hash, site_origin, expires_at, conversation_scope)
        VALUES (:id,:tenant,:customer,:widget,:hash,:origin,:expires,:conversation)"""),
        {
            "id": uuid4(),
            "tenant": row.tenant_id,
            "customer": row.customer_id,
            "widget": row.widget_id,
            "hash": token_hash(access_token),
            "origin": row.site_origin,
            "expires": expires_at,
            "conversation": row.conversation_id,
        },
    )
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return CustomerToken(
        access_token=access_token, expires_at=expires_at, conversation_id=row.conversation_id
    )


@router.post("/email/confirm", operation_id="confirmCustomerEmail")
def confirm_email(
    body: TokenInput,
    response: Response,
    db: Annotated[Session, Depends(get_session)],
) -> CustomerToken:
    return consume_link(db, response, body.token, "confirm")


@router.post("/links/exchange", operation_id="exchangeCustomerLink")
def exchange_link(
    body: TokenInput,
    response: Response,
    db: Annotated[Session, Depends(get_session)],
) -> CustomerToken:
    return consume_link(db, response, body.token, "exchange")
