"""Support policy and elapsed SLA time on the tenant's local calendar."""

import json
from collections import defaultdict
from datetime import UTC, datetime, time, timedelta
from itertools import pairwise
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from ips.core.db import get_session
from ips.core.ports import DTO, TenantContext
from ips.platform.audit import record
from ips.platform.auth import require_csrf
from ips.platform.settings import admin, errors

router = APIRouter(prefix="/api/v1/organization", tags=["B"], responses=errors)
MINUTE = timedelta(minutes=1)
DAY = timedelta(days=1)


class WorkWindow(DTO):
    day_of_week: int = Field(ge=0, le=6, strict=True)
    start: str = Field(pattern=r"^[0-2][0-9]:[0-5][0-9]$")
    end: str = Field(pattern=r"^[0-2][0-9]:[0-5][0-9]$")

    @model_validator(mode="after")
    def validate_window(self):
        if time.fromisoformat(self.start) >= time.fromisoformat(self.end):
            raise ValueError("Start must be earlier than end")
        return self


class SupportPolicy(DTO):
    timezone: str
    working_hours: list[WorkWindow]
    first_response_minutes: int = Field(ge=1, strict=True)
    resolution_minutes: int = Field(ge=1, strict=True)
    urgent_first_response_minutes: int = Field(ge=1, strict=True)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("Expected an IANA timezone") from None
        return value

    @model_validator(mode="after")
    def validate_schedule(self):
        ordered = sorted(self.working_hours, key=lambda x: (x.day_of_week, x.start))
        for first, second in pairwise(ordered):
            if first.day_of_week == second.day_of_week and first.end > second.start:
                raise ValueError("Working windows must not overlap")
        return self


DEFAULT_POLICY = SupportPolicy(
    timezone="UTC",
    working_hours=[],
    first_response_minutes=60,
    resolution_minutes=480,
    urgent_first_response_minutes=15,
)


def working_time(policy: SupportPolicy, start: datetime, end: datetime) -> timedelta:
    """Count actual elapsed time inside recurring wall-clock windows.

    An empty schedule means 24/7 support. Skipped wall times contribute zero;
    repeated wall times contribute the time worked in each occurrence.
    """
    if start.utcoffset() is None or end.utcoffset() is None:
        raise ValueError("SLA timestamps must be timezone-aware")
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    if end <= start:
        return timedelta()
    if not policy.working_hours:
        return end - start

    zone = ZoneInfo(policy.timezone)
    schedule: dict[int, list[tuple[time, time]]] = defaultdict(list)
    for window in policy.working_hours:
        schedule[window.day_of_week].append(
            (time.fromisoformat(window.start), time.fromisoformat(window.end))
        )
    elapsed = timedelta()
    day, last_day = start.astimezone(zone).date(), end.astimezone(zone).date()
    while day <= last_day:
        tomorrow = day + DAY
        windows = schedule[day.weekday()]
        if windows:
            midnight = datetime.combine(day, time.min, zone).astimezone(UTC)
            next_midnight = datetime.combine(tomorrow, time.min, zone).astimezone(UTC)
            if next_midnight - midnight == DAY:
                for opening, closing in windows:
                    opened = datetime.combine(day, opening, zone).astimezone(UTC)
                    closed = datetime.combine(day, closing, zone).astimezone(UTC)
                    elapsed += max(timedelta(), min(end, closed) - max(start, opened))
            else:
                # On a transition day inspect actual UTC minutes, including both folds.
                cursor = max(start, midnight).replace(second=0, microsecond=0)
                stop = min(end, next_midnight)
                while cursor < stop:
                    local = cursor.astimezone(zone)
                    if local.date() == day and any(a <= local.time() < b for a, b in windows):
                        elapsed += min(stop, cursor + MINUTE) - max(start, cursor)
                    cursor += MINUTE
        day = tomorrow
    return elapsed


def sla_breaches(
    policy: SupportPolicy,
    *,
    created_at: datetime,
    first_response_at: datetime | None,
    resolved_at: datetime | None,
    urgent: bool,
    now: datetime,
) -> tuple[bool, bool]:
    """Return first-response and resolution breaches, including outstanding deadlines."""
    target = policy.urgent_first_response_minutes if urgent else policy.first_response_minutes
    return (
        working_time(policy, created_at, first_response_at or now).total_seconds() > target * 60,
        working_time(policy, created_at, resolved_at or now).total_seconds()
        > policy.resolution_minutes * 60,
    )


def get_policy(db: Session, tenant_id: UUID) -> SupportPolicy:
    row = db.execute(
        text("SELECT settings->'support_policy' FROM organizations WHERE id=:tenant"),
        {"tenant": tenant_id},
    ).first()
    if row is None:
        raise HTTPException(404, "Organization unavailable")
    return SupportPolicy.model_validate(row[0]) if row[0] is not None else DEFAULT_POLICY


@router.get("/support-policy", operation_id="getSupportPolicy")
def get_support_policy(
    ctx: Annotated[TenantContext, Depends(admin)], db: Annotated[Session, Depends(get_session)]
) -> SupportPolicy:
    return get_policy(db, ctx.tenant_id)


@router.put(
    "/support-policy", operation_id="updateSupportPolicy", dependencies=[Depends(require_csrf)]
)
def update_support_policy(
    body: SupportPolicy,
    ctx: Annotated[TenantContext, Depends(admin)],
    db: Annotated[Session, Depends(get_session)],
) -> SupportPolicy:
    updated = db.execute(
        text("""UPDATE organizations
            SET settings=settings || CAST(:policy AS jsonb) WHERE id=:tenant RETURNING id"""),
        {"tenant": ctx.tenant_id, "policy": json.dumps({"support_policy": body.model_dump()})},
    ).first()
    if updated is None:
        raise HTTPException(404, "Organization unavailable")
    record(db, ctx.tenant_id, ctx.user_id, "organization.update", ctx.tenant_id)
    db.commit()
    return body
