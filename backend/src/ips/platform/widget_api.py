"""Tenant-scoped widget configuration and customer sessions."""

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from ips.core.db import get_session
from ips.core.ports import CustomerPrincipal, TenantContext
from ips.platform.auth import require_csrf, require_role
from ips.platform.identity import IdentityError, decode_identity, derive_widget_secret
from ips.platform.schemas import ErrorResponse
from ips.platform.widgets import normalize_origin

errors: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 429, 503)
}
staff_router = APIRouter(prefix="/api/v1/widgets", tags=["B"], responses=errors)
public_router = APIRouter(prefix="/api/v1/public", tags=["B"], responses=errors)
admin = require_role("admin")
bearer = HTTPBearer(scheme_name="customerBearer", auto_error=False)
OriginValue = Annotated[str, Field(json_schema_extra={"format": "uri"})]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WidgetInput(Strict):
    name: str = Field(min_length=1, max_length=120)
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    greeting: str = Field(max_length=500)
    allowed_origins: list[OriginValue]
    section_ids: list[UUID]

    @field_validator("allowed_origins")
    @classmethod
    def check_origins(cls, value: list[str]) -> list[str]:
        return [normalize_origin(origin) for origin in value]

    @field_validator("section_ids")
    @classmethod
    def sections_not_yet_available(cls, value: list[UUID]) -> list[UUID]:
        if value:
            raise ValueError("Knowledge section bindings are not available yet")
        return value


class Widget(Strict):
    id: UUID
    public_key: str
    name: str
    color: str
    greeting: str
    allowed_origins: list[str]
    section_ids: list[UUID]
    enabled: bool


class WidgetPage(Strict):
    items: list[Widget]
    next_cursor: str | None


class WidgetSigningKey(Strict):
    key: str
    algorithm: Literal["HMAC-SHA256"]


class PublicSessionInput(Strict):
    signed_identity: str | None = None


class CurrentPublicSession(Strict):
    expires_at: datetime
    customer_id: UUID
    widget_id: UUID
    site_origin: str
    name: str
    color: str
    greeting: str


class PublicSession(CurrentPublicSession):
    access_token: str


def widget_from_row(row: Any) -> Widget:
    return Widget(**dict(row), section_ids=[])


@staff_router.post(
    "",
    operation_id="createWidget",
    status_code=201,
    response_model=Widget,
    dependencies=[Depends(require_csrf)],
)
def create_widget(
    body: WidgetInput,
    ctx: Annotated[TenantContext, Depends(admin)],
    db: Annotated[Session, Depends(get_session)],
) -> Widget:
    row = (
        db.execute(
            text("""INSERT INTO widgets (id, tenant_id, name, color, greeting, allowed_origins, public_key)
        VALUES (:id, :tenant, :name, :color, :greeting, :origins, :key)
        RETURNING id, public_key, name, color, greeting, allowed_origins, enabled"""),
            {
                "id": uuid4(),
                "tenant": ctx.tenant_id,
                "name": body.name,
                "color": body.color,
                "greeting": body.greeting,
                "origins": body.allowed_origins,
                "key": secrets.token_urlsafe(24),
            },
        )
        .mappings()
        .one()
    )
    db.commit()
    return widget_from_row(row)


@staff_router.put(
    "/{widget_id}",
    operation_id="updateWidget",
    response_model=Widget,
    dependencies=[Depends(require_csrf)],
)
def update_widget(
    widget_id: UUID,
    body: WidgetInput,
    ctx: Annotated[TenantContext, Depends(admin)],
    db: Annotated[Session, Depends(get_session)],
) -> Widget:
    row = (
        db.execute(
            text("""UPDATE widgets SET name=:name, color=:color, greeting=:greeting, allowed_origins=:origins
        WHERE id=:id AND tenant_id=:tenant RETURNING id, public_key, name, color, greeting, allowed_origins, enabled"""),
            {
                "id": widget_id,
                "tenant": ctx.tenant_id,
                "name": body.name,
                "color": body.color,
                "greeting": body.greeting,
                "origins": body.allowed_origins,
            },
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, "Widget unavailable")
    db.commit()
    return widget_from_row(row)


@staff_router.post(
    "/{widget_id}/signing-key",
    operation_id="getWidgetSigningKey",
    response_model=WidgetSigningKey,
    dependencies=[Depends(require_csrf)],
)
def get_widget_signing_key(
    widget_id: UUID,
    request: Request,
    response: Response,
    ctx: Annotated[TenantContext, Depends(admin)],
    db: Annotated[Session, Depends(get_session)],
) -> WidgetSigningKey:
    row = db.execute(
        text("SELECT id FROM widgets WHERE id=:id AND tenant_id=:tenant AND enabled=true"),
        {"id": widget_id, "tenant": ctx.tenant_id},
    ).first()
    if row is None:
        raise HTTPException(404, "Widget unavailable")
    response.headers["Cache-Control"] = "no-store"
    secret = derive_widget_secret(request.app.state.settings.session_secret, widget_id)
    encoded = base64.urlsafe_b64encode(secret).rstrip(b"=").decode("ascii")
    return WidgetSigningKey(key=encoded, algorithm="HMAC-SHA256")


@staff_router.get("", operation_id="listWidgets", response_model=WidgetPage)
def list_widgets(
    ctx: Annotated[TenantContext, Depends(admin)],
    db: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: Annotated[str | None, WithJsonSchema({"type": "string"})] = None,
) -> WidgetPage:
    try:
        after = UUID(cursor) if cursor else UUID(int=0)
    except ValueError as exc:
        raise HTTPException(422, "Invalid cursor") from exc
    rows = (
        db.execute(
            text("""SELECT id, public_key, name, color, greeting, allowed_origins, enabled FROM widgets
        WHERE tenant_id=:tenant AND id>:after ORDER BY id LIMIT :limit"""),
            {"tenant": ctx.tenant_id, "after": after, "limit": limit + 1},
        )
        .mappings()
        .all()
    )
    return WidgetPage(
        items=[widget_from_row(row) for row in rows[:limit]],
        next_cursor=str(rows[limit - 1]["id"]) if len(rows) > limit else None,
    )


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@public_router.post(
    "/widgets/{widget_key}/sessions",
    operation_id="createPublicSession",
    status_code=201,
    response_model=PublicSession,
)
def create_public_session(
    widget_key: str,
    body: PublicSessionInput,
    request: Request,
    origin: Annotated[str, Header(alias="Origin", json_schema_extra={"format": "uri"})],
    db: Annotated[Session, Depends(get_session)],
) -> PublicSession:
    row = (
        db.execute(
            text("""SELECT id, tenant_id, name, color, greeting, allowed_origins FROM widgets
        WHERE public_key=:key AND enabled=true"""),
            {"key": widget_key},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, "Widget unavailable")
    if origin not in row["allowed_origins"]:
        raise HTTPException(403, "Origin unavailable")
    external_id = None
    if body.signed_identity is not None:
        try:
            assertion = decode_identity(
                body.signed_identity,
                derive_widget_secret(request.app.state.settings.session_secret, row["id"]),
                widget_key,
            )
        except IdentityError as exc:
            raise HTTPException(401, "Invalid identity assertion") from exc
        expires = datetime.fromtimestamp(assertion["expires_at"], UTC)
        nonce = db.execute(
            text("""INSERT INTO identity_nonces (tenant_id, widget_id, nonce_hash, expires_at)
            VALUES (:tenant,:widget,:nonce,:expires) ON CONFLICT DO NOTHING RETURNING nonce_hash"""),
            {
                "tenant": row["tenant_id"],
                "widget": row["id"],
                "nonce": token_hash(assertion["nonce"]),
                "expires": expires,
            },
        ).first()
        if nonce is None:
            raise HTTPException(401, "Invalid identity assertion")
        external_id = assertion["external_id"]
    customer_id = uuid4()
    if external_id is not None:
        customer_id = db.execute(
            text("""INSERT INTO customers (id,tenant_id,external_id) VALUES (:id,:tenant,:external)
            ON CONFLICT (tenant_id,external_id) WHERE external_id IS NOT NULL DO UPDATE SET external_id=EXCLUDED.external_id RETURNING id"""),
            {"id": customer_id, "tenant": row["tenant_id"], "external": external_id},
        ).scalar_one()
    else:
        db.execute(
            text("INSERT INTO customers (id,tenant_id) VALUES (:id,:tenant)"),
            {"id": customer_id, "tenant": row["tenant_id"]},
        )
    token = secrets.token_urlsafe(32)
    expiry = datetime.now(UTC) + timedelta(days=90)
    db.execute(
        text("""INSERT INTO customer_sessions (id,tenant_id,customer_id,widget_id,token_hash,site_origin,expires_at)
        VALUES (:id,:tenant,:customer,:widget,:token,:origin,:expires)"""),
        {
            "id": uuid4(),
            "tenant": row["tenant_id"],
            "customer": customer_id,
            "widget": row["id"],
            "token": token_hash(token),
            "origin": origin,
            "expires": expiry,
        },
    )
    db.commit()
    return PublicSession(
        access_token=token,
        expires_at=expiry,
        customer_id=customer_id,
        widget_id=row["id"],
        site_origin=origin,
        name=row["name"],
        color=row["color"],
        greeting=row["greeting"],
    )


def customer_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)],
    db: Annotated[Session, Depends(get_session)],
) -> CustomerPrincipal:
    """Resolve the authenticated customer's scope exclusively from server-side state."""
    if credentials is None or len(credentials.credentials) > 256:
        raise HTTPException(401, "Unauthorized")
    row = (
        db.execute(
            text("""SELECT s.tenant_id, s.customer_id, s.widget_id, s.conversation_scope
                FROM customer_sessions s JOIN widgets w
                  ON w.tenant_id=s.tenant_id AND w.id=s.widget_id
                WHERE s.token_hash=:token AND s.expires_at > now() AND w.enabled=true AND s.site_origin=ANY(w.allowed_origins)"""),
            {"token": token_hash(credentials.credentials)},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(401, "Unauthorized")
    return CustomerPrincipal(**dict(row))


@public_router.get("/session", operation_id="getPublicSession", response_model=CurrentPublicSession)
def get_public_session(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)],
    db: Annotated[Session, Depends(get_session)],
) -> CurrentPublicSession:
    if credentials is None or len(credentials.credentials) > 256:
        raise HTTPException(401, "Unauthorized")
    row = (
        db.execute(
            text("""SELECT s.expires_at,s.customer_id,s.widget_id,s.site_origin,w.name,w.color,w.greeting
        FROM customer_sessions s JOIN widgets w ON w.tenant_id=s.tenant_id AND w.id=s.widget_id
        WHERE s.token_hash=:token AND s.expires_at > now() AND w.enabled=true AND s.site_origin=ANY(w.allowed_origins)"""),
            {"token": token_hash(credentials.credentials)},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(401, "Unauthorized")
    return CurrentPublicSession(**dict(row))
