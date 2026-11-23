"""Tenant-scoped daily request reservations using PostgreSQL row locks."""

from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text

from ips.core.ports import Reservation, Usage
from ips.platform.settings import get_limits


class PostgresUsageBudget:
    def __init__(self, sessions, *, lease_seconds=120):
        if lease_seconds < 1:
            raise ValueError("Invalid reservation lease")
        self.sessions = sessions
        self.lease_seconds = lease_seconds

    def reserve(self, tenant_id: UUID, run_id: UUID) -> Reservation:
        with self.sessions.begin() as db:
            limits = get_limits(db, tenant_id, lock=True)
            db.execute(
                text(
                    "UPDATE usage_reservations SET state='expired' WHERE tenant_id=:tenant AND state='reserved' AND expires_at<=now()"
                ),
                {"tenant": tenant_id},
            )
            previous = (
                db.execute(
                    text(
                        "SELECT id,state FROM usage_reservations WHERE tenant_id=:tenant AND run_id=:run"
                    ),
                    {"tenant": tenant_id, "run": run_id},
                )
                .mappings()
                .first()
            )
            if previous is not None and previous["state"] in ("reserved", "succeeded"):
                return Reservation(id=previous["id"], tenant_id=tenant_id, run_id=run_id)
            count = db.execute(
                text(
                    "SELECT count(*) FROM usage_reservations WHERE tenant_id=:tenant AND usage_day=(now() AT TIME ZONE 'UTC')::date AND state IN ('reserved','succeeded')"
                ),
                {"tenant": tenant_id},
            ).scalar_one()
            if count >= limits.ai_daily_limit:
                raise HTTPException(429, "AI quota exceeded")
            reservation_id = uuid4()
            db.execute(
                text("""INSERT INTO usage_reservations (id,tenant_id,run_id,usage_day,state,expires_at)
                VALUES (:id,:tenant,:run,(now() AT TIME ZONE 'UTC')::date,'reserved',now()+(:lease * interval '1 second'))
                ON CONFLICT (tenant_id,run_id) DO UPDATE SET id=EXCLUDED.id, usage_day=EXCLUDED.usage_day,
                state='reserved', expires_at=EXCLUDED.expires_at, input_tokens=0,output_tokens=0"""),
                {
                    "id": reservation_id,
                    "tenant": tenant_id,
                    "run": run_id,
                    "lease": self.lease_seconds,
                },
            )
            return Reservation(id=reservation_id, tenant_id=tenant_id, run_id=run_id)

    def settle(self, reservation: Reservation, usage: Usage, failed: bool) -> None:
        with self.sessions.begin() as db:
            get_limits(db, reservation.tenant_id, lock=True)
            db.execute(
                text("""UPDATE usage_reservations SET state=:state,input_tokens=:input,output_tokens=:output
                WHERE id=:id AND tenant_id=:tenant AND run_id=:run AND state='reserved' AND expires_at>now()"""),
                {
                    "state": "failed" if failed else "succeeded",
                    "input": usage.input_tokens,
                    "output": usage.output_tokens,
                    "id": reservation.id,
                    "tenant": reservation.tenant_id,
                    "run": reservation.run_id,
                },
            )
