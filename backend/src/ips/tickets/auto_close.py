"""Close resolved tickets after the tenant's configured grace period."""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import Connection, text


def close_due_tickets(
    conn: Connection, tenant_id: UUID, now: datetime, hours: int, *, limit: int = 100
) -> int:
    try:
        cutoff = now - timedelta(hours=hours)
    except OverflowError:
        return 0
    # Closing for inactivity does not record a customer-confirmed resolution.
    return conn.execute(
        text("""WITH due AS (
            SELECT id FROM tickets WHERE tenant_id=:tenant AND status='resolved'
                AND resolved_at<=:cutoff
            ORDER BY resolved_at,id LIMIT :limit FOR UPDATE SKIP LOCKED
        )
        UPDATE tickets t
        SET status='closed', closed_at=:now, updated_at=:now, version=t.version+1
        FROM due WHERE t.tenant_id=:tenant AND t.id=due.id"""),
        {"tenant": tenant_id, "now": now, "cutoff": cutoff, "limit": limit},
    ).rowcount
