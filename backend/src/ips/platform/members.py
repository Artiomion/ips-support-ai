"""Tenant-scoped specialists, categories and one-time invitations."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field, WithJsonSchema, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ips.core.db import get_session
from ips.core.jobs import enqueue
from ips.core.ports import TenantContext
from ips.platform.auth import (
    check_origin,
    create_session,
    csrf_for,
    hash_token,
    hasher,
    require_csrf,
    require_role,
    tenant_context,
)
from ips.platform.notifications import invitation_token
from ips.platform.router import set_cookie
from ips.platform.schemas import ErrorResponse, StaffSession, TenantStaffSession

errors: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 429, 503)
}
router = APIRouter(prefix="/api/v1", tags=["B"], responses=errors)
admin = require_role("admin")
Cursor = Annotated[str | None, WithJsonSchema({"type": "string"})]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Member(Strict):
    id: UUID
    email: EmailStr
    role: Literal["admin", "specialist"]
    available: bool
    max_active: int = Field(ge=1, le=100)
    category_ids: list[UUID]


class MemberPage(Strict):
    items: list[Member]
    next_cursor: str | None


class MemberUpdate(Strict):
    available: Annotated[bool | None, WithJsonSchema({"type": "boolean"})] = None
    max_active: Annotated[
        int | None,
        Field(ge=1, le=100),
        WithJsonSchema({"type": "integer", "minimum": 1, "maximum": 100}),
    ] = None
    category_ids: Annotated[
        list[UUID] | None,
        WithJsonSchema({"type": "array", "items": {"type": "string", "format": "uuid"}}),
    ] = None
    role: Annotated[
        Literal["admin", "specialist"] | None,
        WithJsonSchema({"type": "string", "enum": ["admin", "specialist"]}),
    ] = None

    @field_validator("available", "max_active", "category_ids", "role", mode="before")
    @classmethod
    def reject_null(cls, value):
        if value is None:
            raise ValueError("Explicit null is not supported")
        return value


class Category(Strict):
    id: UUID
    name: str


class CategoryInput(Strict):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Category name is required")
        return value


class CategoryPage(Strict):
    items: list[Category]
    next_cursor: str | None


class InviteInput(Strict):
    email: EmailStr
    role: Literal["specialist", "admin"]


class InviteResult(Strict):
    invitation_id: UUID
    delivery: Literal["queued"]


class AcceptInviteInput(Strict):
    token: str
    password: str = Field(min_length=12, max_length=128)


def cursor_value(cursor: str | None) -> UUID:
    try:
        return UUID(cursor) if cursor else UUID(int=0)
    except ValueError as exc:
        raise HTTPException(422, "Invalid cursor") from exc


def member_row(db: Session, tenant_id: UUID, member_id: UUID) -> Member:
    row = (
        db.execute(
            text("""SELECT m.id,u.normalized_email AS email,m.role,m.available,m.max_active,
        ARRAY(SELECT sc.category_id FROM specialist_categories sc WHERE sc.tenant_id=m.tenant_id AND sc.membership_id=m.id ORDER BY sc.category_id) AS category_ids
        FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.tenant_id=:tenant AND m.id=:id"""),
            {"tenant": tenant_id, "id": member_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, "Member unavailable")
    return Member(**dict(row))


@router.get("/members", operation_id="listMembers", response_model=MemberPage)
def list_members(
    ctx: Annotated[TenantContext, Depends(tenant_context)],
    db: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: Cursor = None,
) -> MemberPage:
    rows = (
        db.execute(
            text(
                "SELECT id FROM memberships WHERE tenant_id=:tenant AND id>:after ORDER BY id LIMIT :limit"
            ),
            {"tenant": ctx.tenant_id, "after": cursor_value(cursor), "limit": limit + 1},
        )
        .scalars()
        .all()
    )
    return MemberPage(
        items=[member_row(db, ctx.tenant_id, mid) for mid in rows[:limit]],
        next_cursor=str(rows[limit - 1]) if len(rows) > limit else None,
    )


@router.patch(
    "/members/{member_id}",
    operation_id="updateMember",
    response_model=Member,
    dependencies=[Depends(require_csrf)],
)
def update_member(
    member_id: UUID,
    body: MemberUpdate,
    ctx: Annotated[TenantContext, Depends(admin)],
    db: Annotated[Session, Depends(get_session)],
) -> Member:
    # Serialize administrator demotions to preserve at least one admin per tenant.
    db.execute(
        text("SELECT id FROM organizations WHERE id=:tenant FOR UPDATE"),
        {"tenant": ctx.tenant_id},
    )
    current = (
        db.execute(
            text("SELECT id, role FROM memberships WHERE tenant_id=:tenant AND id=:id FOR UPDATE"),
            {"tenant": ctx.tenant_id, "id": member_id},
        )
        .mappings()
        .first()
    )
    if current is None:
        raise HTTPException(404, "Member unavailable")
    if body.role == "specialist" and current["role"] == "admin":
        count = db.execute(
            text("SELECT count(*) FROM memberships WHERE tenant_id=:tenant AND role='admin'"),
            {"tenant": ctx.tenant_id},
        ).scalar_one()
        if count <= 1:
            raise HTTPException(409, "Cannot remove the last admin")
    if "category_ids" in body.model_fields_set:
        ids = set(body.category_ids or [])
        found = set(
            db.execute(
                text("SELECT id FROM categories WHERE tenant_id=:tenant AND id=ANY(:ids)"),
                {"tenant": ctx.tenant_id, "ids": list(ids)},
            ).scalars()
        )
        if found != ids:
            raise HTTPException(404, "Category unavailable")
        db.execute(
            text("DELETE FROM specialist_categories WHERE tenant_id=:tenant AND membership_id=:id"),
            {"tenant": ctx.tenant_id, "id": member_id},
        )
        for cid in ids:
            db.execute(
                text(
                    "INSERT INTO specialist_categories (tenant_id,membership_id,category_id) VALUES (:tenant,:id,:category)"
                ),
                {"tenant": ctx.tenant_id, "id": member_id, "category": cid},
            )
    values = body.model_dump(exclude_unset=True, exclude={"category_ids"})
    if values:
        assignments = ",".join(f"{key}=:{key}" for key in values)
        db.execute(
            text(f"UPDATE memberships SET {assignments} WHERE tenant_id=:tenant AND id=:id"),
            {**values, "tenant": ctx.tenant_id, "id": member_id},
        )
    result = member_row(db, ctx.tenant_id, member_id)
    db.commit()
    return result


@router.get("/categories", operation_id="listCategories", response_model=CategoryPage)
def list_categories(
    ctx: Annotated[TenantContext, Depends(tenant_context)],
    db: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: Cursor = None,
) -> CategoryPage:
    rows = (
        db.execute(
            text(
                "SELECT id,name FROM categories WHERE tenant_id=:tenant AND id>:after ORDER BY id LIMIT :limit"
            ),
            {"tenant": ctx.tenant_id, "after": cursor_value(cursor), "limit": limit + 1},
        )
        .mappings()
        .all()
    )
    return CategoryPage(
        items=[Category(**dict(row)) for row in rows[:limit]],
        next_cursor=str(rows[limit - 1]["id"]) if len(rows) > limit else None,
    )


@router.post(
    "/categories",
    operation_id="createCategory",
    status_code=201,
    response_model=Category,
    dependencies=[Depends(require_csrf)],
)
def create_category(
    body: CategoryInput,
    ctx: Annotated[TenantContext, Depends(admin)],
    db: Annotated[Session, Depends(get_session)],
) -> Category:
    identifier = uuid4()
    try:
        db.execute(
            text("INSERT INTO categories (id,tenant_id,name) VALUES (:id,:tenant,:name)"),
            {"id": identifier, "tenant": ctx.tenant_id, "name": body.name},
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Category already exists") from exc
    return Category(id=identifier, name=body.name)


@router.post(
    "/members/invitations",
    operation_id="inviteMember",
    status_code=201,
    response_model=InviteResult,
    dependencies=[Depends(require_csrf)],
)
def invite_member(
    body: InviteInput,
    request: Request,
    ctx: Annotated[TenantContext, Depends(admin)],
    db: Annotated[Session, Depends(get_session)],
) -> InviteResult:
    email = str(body.email).strip().casefold()
    exists = db.execute(
        text(
            "SELECT 1 FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.tenant_id=:tenant AND u.normalized_email=:email"
        ),
        {"tenant": ctx.tenant_id, "email": email},
    ).first()
    if exists:
        raise HTTPException(409, "Member already exists")
    identifier = uuid4()
    token = invitation_token(request.app.state.settings.session_secret, identifier)
    db.execute(
        text(
            "INSERT INTO invitations (id,tenant_id,email,role,token_hash,expires_at) VALUES (:id,:tenant,:email,:role,:token,:expires)"
        ),
        {
            "id": identifier,
            "tenant": ctx.tenant_id,
            "email": email,
            "role": body.role,
            "token": hash_token(token),
            "expires": datetime.now(UTC) + timedelta(days=7),
        },
    )
    enqueue(
        db.connection(),
        ctx.tenant_id,
        "invitation",
        {"invitation_id": str(identifier), "tenant_id": str(ctx.tenant_id)},
        f"invitation:{identifier}",
    )
    db.commit()
    return InviteResult(invitation_id=identifier, delivery="queued")


@router.post(
    "/auth/invitations/accept", operation_id="acceptInvitation", response_model=StaffSession
)
def accept_invitation(
    body: AcceptInviteInput,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_session)],
) -> StaffSession:
    check_origin(request)
    if len(body.token) > 256:
        raise HTTPException(401, "Invalid invitation")
    invite = (
        db.execute(
            text("""SELECT id,tenant_id,email,role FROM invitations WHERE token_hash=:token
        AND expires_at > now() AND accepted_at IS NULL FOR UPDATE"""),
            {"token": hash_token(body.token)},
        )
        .mappings()
        .first()
    )
    if invite is None:
        raise HTTPException(401, "Invalid invitation")
    user = (
        db.execute(
            text("SELECT id,password_hash FROM users WHERE normalized_email=:email"),
            {"email": invite["email"]},
        )
        .mappings()
        .first()
    )
    if user:
        try:
            if not hasher.verify(user["password_hash"], body.password):
                raise HTTPException(401, "Invalid invitation")
        except (VerifyMismatchError, InvalidHashError) as exc:
            raise HTTPException(401, "Invalid invitation") from exc
        user_id = user["id"]
    else:
        user_id = uuid4()
        try:
            db.execute(
                text(
                    "INSERT INTO users (id,normalized_email,password_hash) VALUES (:id,:email,:password)"
                ),
                {"id": user_id, "email": invite["email"], "password": hasher.hash(body.password)},
            )
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(409, "Invitation unavailable") from exc
    member_id = uuid4()
    try:
        db.execute(
            text(
                "INSERT INTO memberships (id,tenant_id,user_id,role) VALUES (:id,:tenant,:user,:role)"
            ),
            {
                "id": member_id,
                "tenant": invite["tenant_id"],
                "user": user_id,
                "role": invite["role"],
            },
        )
        db.execute(
            text("UPDATE invitations SET accepted_at=now() WHERE id=:id"), {"id": invite["id"]}
        )
        token = create_session(db, user_id, invite["tenant_id"])
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Invitation unavailable") from exc
    set_cookie(response, request, token)
    return TenantStaffSession(
        scope="tenant",
        user_id=user_id,
        tenant_id=invite["tenant_id"],
        membership_id=member_id,
        role=invite["role"],
        csrf_token=csrf_for(token, request.app.state.settings.session_secret),
    )
