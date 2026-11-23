"""Tenant settings and honest capability responses."""

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from ips.core.db import get_session
from ips.core.ports import CustomerPrincipal, TenantContext
from ips.platform.audit import record
from ips.platform.auth import require_csrf, require_role, tenant_context
from ips.platform.schemas import ErrorResponse
from ips.platform.widget_api import customer_principal

errors: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 429, 503)
}
router = APIRouter(prefix="/api/v1", tags=["B"], responses=errors)
admin = require_role("admin")
DEFAULTS = {
    "allow_general_knowledge": False,
    "language": "ru",
    "ai_style": "helpful",
    "ai_daily_limit": 100,
    "max_widgets": 5,
    "max_staff": 10,
    "retention_days": 90,
    "auto_close_hours": 72,
}


class OrganizationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    allow_general_knowledge: bool
    language: str
    ai_style: str
    ai_daily_limit: int = Field(ge=0)
    max_widgets: int = Field(ge=1)
    max_staff: int = Field(ge=1)
    retention_days: int = Field(ge=1)
    auto_close_hours: int = Field(ge=1)


class OrganizationSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Annotated[str | None, WithJsonSchema({"type": "string"})] = None
    allow_general_knowledge: Annotated[bool | None, WithJsonSchema({"type": "boolean"})] = None
    language: Annotated[str | None, WithJsonSchema({"type": "string"})] = None
    ai_style: Annotated[str | None, WithJsonSchema({"type": "string"})] = None
    retention_days: Annotated[int | None, WithJsonSchema({"type": "integer", "minimum": 1})] = None
    auto_close_hours: Annotated[int | None, WithJsonSchema({"type": "integer", "minimum": 1})] = (
        None
    )

    @field_validator(
        "name",
        "allow_general_knowledge",
        "language",
        "ai_style",
        "retention_days",
        "auto_close_hours",
        mode="before",
    )
    @classmethod
    def validate_value(cls, value):
        if value is None or isinstance(value, str) and (not value.strip() or len(value) > 120):
            raise ValueError("Invalid value")
        if isinstance(value, int) and not isinstance(value, bool) and value < 1:
            raise ValueError("Invalid value")
        return value


class Capabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")
    features: list[
        Literal[
            "txt_import",
            "document_import",
            "vision",
            "staff_assistant",
            "knowledge_suggestions",
            "sla",
            "incidents",
            "complaints",
        ]
    ]
    ai_provider: Literal["fake", "http"]
    vision_supported: bool


def get_limits(db, tenant_id, *, lock=False):
    query = "SELECT name,settings FROM organizations WHERE id=:id" + (" FOR UPDATE" if lock else "")
    row = db.execute(text(query), {"id": tenant_id}).mappings().first()
    if row is None:
        raise HTTPException(404, "Organization unavailable")
    saved = {key: value for key, value in row["settings"].items() if key in DEFAULTS}
    return OrganizationSettings.model_validate({**DEFAULTS, **saved, "name": row["name"]})


@router.get("/organization", operation_id="getOrganization", response_model=OrganizationSettings)
def get_organization(
    ctx: Annotated[TenantContext, Depends(admin)], db: Annotated[Session, Depends(get_session)]
):
    return get_limits(db, ctx.tenant_id)


@router.patch(
    "/organization",
    operation_id="updateOrganization",
    response_model=OrganizationSettings,
    dependencies=[Depends(require_csrf)],
)
def update_organization(
    body: OrganizationSettingsUpdate,
    ctx: Annotated[TenantContext, Depends(admin)],
    db: Annotated[Session, Depends(get_session)],
):
    current = get_limits(db, ctx.tenant_id, lock=True)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "No changes")
    name = changes.pop("name", current.name)
    db.execute(
        text(
            "UPDATE organizations SET name=:name, settings=settings || CAST(:changes AS jsonb) WHERE id=:id"
        ),
        {"name": name, "changes": json.dumps(changes), "id": ctx.tenant_id},
    )
    record(db, ctx.tenant_id, ctx.user_id, "organization.update", ctx.tenant_id)
    result = get_limits(db, ctx.tenant_id)
    db.commit()
    return result


@router.get("/capabilities", operation_id="getStaffCapabilities", response_model=Capabilities)
def staff_capabilities(ctx: Annotated[TenantContext, Depends(tenant_context)]):
    return Capabilities(features=["txt_import"], ai_provider="fake", vision_supported=False)


@router.get(
    "/public/capabilities", operation_id="getCustomerCapabilities", response_model=Capabilities
)
def customer_capabilities(principal: Annotated[CustomerPrincipal, Depends(customer_principal)]):
    return Capabilities(features=[], ai_provider="fake", vision_supported=False)
