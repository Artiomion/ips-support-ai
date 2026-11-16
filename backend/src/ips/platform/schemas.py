from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorDetail(StrictModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(StrictModel):
    error: ErrorDetail


class RegisterInput(StrictModel):
    organization_name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)

    @field_validator("organization_name")
    @classmethod
    def validate_organization_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Organization name must not be blank")
        return value


class LoginInput(StrictModel):
    email: EmailStr
    password: str
    organization_id: UUID | None = None


class TenantStaffSession(StrictModel):
    scope: Literal["tenant"]
    user_id: UUID
    tenant_id: UUID
    membership_id: UUID
    role: Literal["admin", "specialist"]
    csrf_token: str


class PlatformSession(StrictModel):
    scope: Literal["platform"]
    user_id: UUID
    role: Literal["superadmin"]
    csrf_token: str


StaffSession = Annotated[TenantStaffSession | PlatformSession, Field(discriminator="scope")]
