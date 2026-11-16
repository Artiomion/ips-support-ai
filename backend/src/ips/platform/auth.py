"""Staff session and authorization primitives."""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from fastapi import Depends, Header, HTTPException, Request, Security
from fastapi.security import APIKeyCookie
from sqlalchemy import text
from sqlalchemy.orm import Session

from ips.core.db import get_session
from ips.core.ports import TenantContext
from ips.platform.schemas import PlatformSession, TenantStaffSession

COOKIE = "ips_session"
cookie_scheme = APIKeyCookie(name=COOKIE, scheme_name="staffCookie", auto_error=False)
hasher = PasswordHasher()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_for(token: str, secret: str) -> str:
    return hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def check_origin(request: Request) -> None:
    if request.headers.get("origin") != request.app.state.settings.web_origin:
        raise HTTPException(403, "Invalid origin")


def create_session(db: Session, user_id: UUID, tenant_id: UUID | None) -> str:
    token = secrets.token_urlsafe(32)
    db.execute(
        text(
            "INSERT INTO staff_sessions (id, token_hash, user_id, active_tenant_id, expires_at) VALUES (:id, :token_hash, :user_id, :tenant_id, :expires_at)"
        ),
        {
            "id": uuid4(),
            "token_hash": hash_token(token),
            "user_id": user_id,
            "tenant_id": tenant_id,
            "expires_at": datetime.now(UTC) + timedelta(hours=24),
        },
    )
    return token


def get_staff_session(
    request: Request,
    token: Annotated[str | None, Security(cookie_scheme)],
    db: Annotated[Session, Depends(get_session)],
) -> TenantStaffSession | PlatformSession:
    if not token or len(token) > 256:
        raise HTTPException(401, "Unauthorized")
    row = (
        db.execute(
            text("""SELECT s.user_id, s.active_tenant_id, u.is_superadmin, m.id AS membership_id, m.role
        FROM staff_sessions s JOIN users u ON u.id = s.user_id
        LEFT JOIN memberships m ON m.user_id = s.user_id AND m.tenant_id = s.active_tenant_id
        WHERE s.token_hash = :token_hash AND s.expires_at > now()"""),
            {"token_hash": hash_token(token)},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(401, "Unauthorized")
    csrf = csrf_for(token, request.app.state.settings.session_secret)
    if row["is_superadmin"] and row["active_tenant_id"] is None:
        return PlatformSession(
            scope="platform", user_id=row["user_id"], role="superadmin", csrf_token=csrf
        )
    if row["membership_id"] is None:
        raise HTTPException(401, "Unauthorized")
    return TenantStaffSession(
        scope="tenant",
        user_id=row["user_id"],
        tenant_id=row["active_tenant_id"],
        membership_id=row["membership_id"],
        role=row["role"],
        csrf_token=csrf,
    )


def tenant_context(
    session: Annotated[TenantStaffSession | PlatformSession, Depends(get_staff_session)],
) -> TenantContext:
    if not isinstance(session, TenantStaffSession):
        raise HTTPException(403, "Tenant membership required")
    return TenantContext(
        tenant_id=session.tenant_id,
        user_id=session.user_id,
        membership_id=session.membership_id,
        role=session.role,
    )


def require_role(role: str):
    def guard(ctx: Annotated[TenantContext, Depends(tenant_context)]) -> TenantContext:
        if ctx.role != role:
            raise HTTPException(403, "Insufficient role")
        return ctx

    return guard


def require_tenant(ctx: TenantContext, tenant_id: UUID) -> None:
    """Hide resources belonging to another tenant from the caller."""
    if ctx.tenant_id != tenant_id:
        raise HTTPException(404, "Resource unavailable")


def require_csrf(
    request: Request,
    csrf_header: Annotated[str, Header(alias="X-CSRF-Token")],
    session: Annotated[TenantStaffSession | PlatformSession, Depends(get_staff_session)],
) -> None:
    check_origin(request)
    if not hmac.compare_digest(csrf_header.encode("utf-8"), session.csrf_token.encode("ascii")):
        raise HTTPException(403, "Invalid CSRF token")
