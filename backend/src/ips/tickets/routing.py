"""Pick an available specialist under deterministic membership locks."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from ips.core.ports import TicketSummary


def route_ticket(
    db: Session, tenant_id: UUID, ticket_id: UUID, expected_version: int
) -> TicketSummary | None:
    # Lock membership rows in the same order in every automatic routing call.
    db.execute(
        text("SELECT id FROM memberships WHERE tenant_id=:tenant ORDER BY id FOR UPDATE"),
        {"tenant": tenant_id},
    ).all()
    ticket = (
        db.execute(
            text("SELECT * FROM tickets WHERE tenant_id=:tenant AND id=:id FOR UPDATE"),
            {"tenant": tenant_id, "id": ticket_id},
        )
        .mappings()
        .first()
    )
    if ticket is None:
        raise HTTPException(404, "Ticket unavailable")
    if (
        ticket["version"] != expected_version
        or ticket["status"] != "waiting_specialist"
        or ticket["assignee_id"] is not None
    ):
        raise HTTPException(409, "Ticket changed or cannot be routed")
    category_clause = ""
    if ticket["category_id"] is not None:
        category_clause = "AND EXISTS (SELECT 1 FROM specialist_categories sc WHERE sc.tenant_id=m.tenant_id AND sc.membership_id=m.id AND sc.category_id=:category)"
    query = (
        """SELECT m.id, m.max_active, (SELECT count(*) FROM tickets t
        WHERE t.tenant_id=m.tenant_id AND t.assignee_id=m.id
        AND t.status NOT IN ('resolved','closed')) AS active
        FROM memberships m WHERE m.tenant_id=:tenant AND m.role='specialist' AND m.available=true """
        + category_clause
        + " ORDER BY active,m.id"
    )
    candidates = (
        db.execute(text(query), {"tenant": tenant_id, "category": ticket["category_id"]})
        .mappings()
        .all()
    )
    chosen = next((entry for entry in candidates if entry["active"] < entry["max_active"]), None)
    if chosen is None:
        return None
    updated = (
        db.execute(
            text("""UPDATE tickets SET assignee_id=:member,status='in_progress',
        version=version+1,updated_at=now() WHERE tenant_id=:tenant AND id=:id RETURNING *"""),
            {"tenant": tenant_id, "id": ticket_id, "member": chosen["id"]},
        )
        .mappings()
        .one()
    )
    return TicketSummary.model_validate({key: updated[key] for key in TicketSummary.model_fields})
