"""Atomic claim of a queued ticket using the caller's database transaction."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from ips.core.ports import TenantContext, TicketSummary


def claim_ticket(
    db: Session, ctx: TenantContext, ticket_id: UUID, expected_version: int
) -> TicketSummary:
    # Membership first, ticket second: every capacity-changing path must use this order.
    member = (
        db.execute(
            text("""SELECT id, available, max_active FROM memberships
        WHERE tenant_id=:tenant AND id=:id AND user_id=:user AND role=:role FOR UPDATE"""),
            {
                "tenant": ctx.tenant_id,
                "id": ctx.membership_id,
                "user": ctx.user_id,
                "role": ctx.role,
            },
        )
        .mappings()
        .first()
    )
    if member is None:
        raise HTTPException(404, "Membership unavailable")
    row = (
        db.execute(
            text("SELECT * FROM tickets WHERE tenant_id=:tenant AND id=:id FOR UPDATE"),
            {"tenant": ctx.tenant_id, "id": ticket_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, "Ticket unavailable")
    # A stale version must not change the assignee or consume capacity.
    if row["version"] != expected_version:
        raise HTTPException(409, "Ticket changed")
    if row["status"] == "in_progress" and row["assignee_id"] == ctx.membership_id:
        return TicketSummary.model_validate({key: row[key] for key in TicketSummary.model_fields})
    if row["status"] != "waiting_specialist" or row["assignee_id"] is not None:
        raise HTTPException(409, "Ticket cannot be claimed")
    if not member["available"]:
        raise HTTPException(409, "Specialist unavailable")
    active = db.execute(
        text("""SELECT count(*) FROM tickets WHERE tenant_id=:tenant AND assignee_id=:member
        AND status NOT IN ('resolved','closed')"""),
        {"tenant": ctx.tenant_id, "member": ctx.membership_id},
    ).scalar_one()
    if active >= member["max_active"]:
        raise HTTPException(409, "Specialist at capacity")
    updated = (
        db.execute(
            text("""UPDATE tickets SET assignee_id=:member,status='in_progress',
        version=version+1,updated_at=now() WHERE tenant_id=:tenant AND id=:id RETURNING *"""),
            {"tenant": ctx.tenant_id, "id": ticket_id, "member": ctx.membership_id},
        )
        .mappings()
        .one()
    )
    return TicketSummary.model_validate({key: updated[key] for key in TicketSummary.model_fields})


def reassign_ticket(
    db: Session, ctx: TenantContext, ticket_id: UUID, expected_version: int, target_id: UUID | None
) -> TicketSummary:
    # A target membership is locked before the ticket, as in claim and routing.
    target = None
    if target_id is not None:
        target = (
            db.execute(
                text("""SELECT id, available, max_active FROM memberships
            WHERE tenant_id=:tenant AND id=:id AND role='specialist' FOR UPDATE"""),
                {"tenant": ctx.tenant_id, "id": target_id},
            )
            .mappings()
            .first()
        )
    row = (
        db.execute(
            text("SELECT * FROM tickets WHERE tenant_id=:tenant AND id=:id FOR UPDATE"),
            {"tenant": ctx.tenant_id, "id": ticket_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, "Ticket unavailable")
    if ctx.role != "admin" and row["assignee_id"] != ctx.membership_id:
        raise HTTPException(403, "Ticket is assigned to another specialist")
    if row["version"] != expected_version:
        raise HTTPException(409, "Ticket changed")
    if row["status"] not in ("waiting_specialist", "in_progress", "waiting_customer"):
        raise HTTPException(409, "Ticket cannot be reassigned")
    if target_id is not None and target is None:
        raise HTTPException(404, "Specialist unavailable")
    if target_id == row["assignee_id"]:
        return TicketSummary.model_validate({key: row[key] for key in TicketSummary.model_fields})
    if target is not None:
        if not target["available"]:
            raise HTTPException(409, "Specialist unavailable")
        active = db.execute(
            text("""SELECT count(*) FROM tickets WHERE tenant_id=:tenant AND assignee_id=:member
            AND status NOT IN ('resolved','closed')"""),
            {"tenant": ctx.tenant_id, "member": target_id},
        ).scalar_one()
        if active >= target["max_active"]:
            raise HTTPException(409, "Specialist at capacity")
    updated = (
        db.execute(
            text("""UPDATE tickets SET assignee_id=:member,status=:status,
        version=version+1,updated_at=now() WHERE tenant_id=:tenant AND id=:id RETURNING *"""),
            {
                "tenant": ctx.tenant_id,
                "id": ticket_id,
                "member": target_id,
                "status": "in_progress" if target_id is not None else "waiting_specialist",
            },
        )
        .mappings()
        .one()
    )
    return TicketSummary.model_validate({key: updated[key] for key in TicketSummary.model_fields})
