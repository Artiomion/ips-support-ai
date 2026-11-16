"""Registration, login and server-side staff sessions."""

from typing import Annotated
from uuid import UUID, uuid4

from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, Security
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ips.core.db import get_session
from ips.platform.auth import (
    COOKIE,
    check_origin,
    cookie_scheme,
    create_session,
    csrf_for,
    get_staff_session,
    hash_token,
    hasher,
    require_csrf,
)
from ips.platform.schemas import (
    ErrorResponse,
    LoginInput,
    PlatformSession,
    RegisterInput,
    StaffSession,
    TenantStaffSession,
)

router = APIRouter(
    prefix="/api/v1/auth",
    tags=["B"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 429, 503)},
)
DUMMY_HASH = hasher.hash("unused-dummy-password-avoid-enumeration")


def set_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        COOKIE,
        token,
        max_age=86400,
        httponly=True,
        secure=request.app.state.settings.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/register", operation_id="register", status_code=201, response_model=StaffSession)
def register(
    body: RegisterInput,
    request: Request,
    response: Response,
    origin: Annotated[str, Header(alias="Origin", json_schema_extra={"format": "uri"})],
    db: Annotated[Session, Depends(get_session)],
) -> StaffSession:
    check_origin(request)
    normalized = str(body.email).strip().casefold()
    tenant_id, user_id, member_id = uuid4(), uuid4(), uuid4()
    try:
        db.execute(
            text("INSERT INTO organizations (id, name) VALUES (:id, :name)"),
            {"id": tenant_id, "name": body.organization_name.strip()},
        )
        db.execute(
            text(
                "INSERT INTO users (id, normalized_email, password_hash) VALUES (:id, :email, :hash)"
            ),
            {"id": user_id, "email": normalized, "hash": hasher.hash(body.password)},
        )
        db.execute(
            text(
                "INSERT INTO memberships (id, tenant_id, user_id, role) VALUES (:id, :tenant, :user, 'admin')"
            ),
            {"id": member_id, "tenant": tenant_id, "user": user_id},
        )
        token = create_session(db, user_id, tenant_id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Registration unavailable") from exc
    set_cookie(response, request, token)
    return TenantStaffSession(
        scope="tenant",
        user_id=user_id,
        tenant_id=tenant_id,
        membership_id=member_id,
        role="admin",
        csrf_token=csrf_for(token, request.app.state.settings.session_secret),
    )


@router.post("/login", operation_id="login", response_model=StaffSession)
def login(
    body: LoginInput,
    request: Request,
    response: Response,
    origin: Annotated[str, Header(alias="Origin", json_schema_extra={"format": "uri"})],
    db: Annotated[Session, Depends(get_session)],
) -> StaffSession:
    check_origin(request)
    row = (
        db.execute(
            text(
                "SELECT id, password_hash, is_superadmin FROM users WHERE normalized_email = :email"
            ),
            {"email": str(body.email).strip().casefold()},
        )
        .mappings()
        .first()
    )
    try:
        valid = hasher.verify(row["password_hash"] if row else DUMMY_HASH, body.password)
    except (VerifyMismatchError, InvalidHashError):
        valid = False
    if not row or not valid:
        raise HTTPException(401, "Invalid credentials")
    user_id: UUID = row["id"]
    if row["is_superadmin"] and body.organization_id is None:
        token = create_session(db, user_id, None)
        db.commit()
        set_cookie(response, request, token)
        return PlatformSession(
            scope="platform",
            user_id=user_id,
            role="superadmin",
            csrf_token=csrf_for(token, request.app.state.settings.session_secret),
        )
    memberships = (
        db.execute(
            text(
                "SELECT id, tenant_id, role FROM memberships WHERE user_id = :user_id AND (CAST(:tenant_id AS uuid) IS NULL OR tenant_id = :tenant_id) ORDER BY tenant_id"
            ),
            {"user_id": user_id, "tenant_id": body.organization_id},
        )
        .mappings()
        .all()
    )
    if not memberships:
        raise HTTPException(404, "Organization unavailable")
    if len(memberships) > 1:
        raise HTTPException(409, "Select organization")
    membership = memberships[0]
    token = create_session(db, user_id, membership["tenant_id"])
    db.commit()
    set_cookie(response, request, token)
    return TenantStaffSession(
        scope="tenant",
        user_id=user_id,
        tenant_id=membership["tenant_id"],
        membership_id=membership["id"],
        role=membership["role"],
        csrf_token=csrf_for(token, request.app.state.settings.session_secret),
    )


@router.get("/me", operation_id="getStaffSession", response_model=StaffSession)
def me(session: Annotated[StaffSession, Depends(get_staff_session)]) -> StaffSession:
    return session


@router.post(
    "/logout", operation_id="logout", status_code=204, dependencies=[Depends(require_csrf)]
)
def logout(
    response: Response,
    token: Annotated[str | None, Security(cookie_scheme)],
    db: Annotated[Session, Depends(get_session)],
) -> Response:
    if token:
        db.execute(
            text("DELETE FROM staff_sessions WHERE token_hash = :token"),
            {"token": hash_token(token)},
        )
        db.commit()
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="lax")
    response.status_code = 204
    return response
