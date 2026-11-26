"""Persist jobs and coordinate workers against PostgreSQL."""

import json
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError


@dataclass(frozen=True)
class Job:
    id: UUID
    tenant_id: UUID
    kind: str
    payload: dict[str, Any]
    idempotency_key: str
    attempts: int


@contextmanager
def lease_job(
    engine: Engine, kinds: list[str], now: datetime, duration: timedelta, max_attempts: int
) -> Generator[tuple[Connection, Job] | None]:
    with engine.connect() as conn:
        key = None
        job = None
        try:
            with conn.begin():
                candidate = conn.execute(
                    text("""SELECT id, status, attempts FROM jobs
                        WHERE kind=ANY(:kinds) AND (
                          (status='pending' AND run_after<=:now)
                          OR (status IN ('running','sending') AND lease_until<=:now)
                        )
                        ORDER BY CASE WHEN status='pending' THEN run_after ELSE lease_until END, id
                        FOR UPDATE SKIP LOCKED LIMIT 1"""),
                    {"kinds": kinds, "now": now},
                ).first()
                if candidate is not None:
                    lock_key = str(candidate.id)
                    locked = conn.scalar(
                        text("SELECT pg_try_advisory_lock(hashtextextended(:key, 309))"),
                        {"key": lock_key},
                    )
                    if locked:
                        key = lock_key
                    if not locked:
                        # A live session still owns the expired lease. Let other jobs proceed.
                        conn.execute(
                            text("UPDATE jobs SET lease_until=:until WHERE id=:id"),
                            {"id": candidate.id, "until": now + duration},
                        )
                    elif candidate.status == "sending" or candidate.attempts >= max_attempts:
                        conn.execute(
                            text("UPDATE jobs SET status=:status, lease_until=NULL WHERE id=:id"),
                            {
                                "id": candidate.id,
                                "status": "uncertain"
                                if candidate.status == "sending"
                                else "failed",
                            },
                        )
                    else:
                        row = (
                            conn.execute(
                                text("""UPDATE jobs SET status='running', lease_until=:until,
                                attempts=attempts+1 WHERE id=:id
                                RETURNING id,tenant_id,kind,payload,idempotency_key,attempts"""),
                                {"id": candidate.id, "until": now + duration},
                            )
                            .mappings()
                            .one()
                        )
                        job = Job(**row)
            yield (conn, job) if job is not None else None
        finally:
            if key is not None:
                # Pooled connections must not keep session advisory locks.
                if conn.in_transaction() or conn.invalidated:
                    conn.invalidate()
                else:
                    try:
                        with conn.begin():
                            conn.execute(
                                text("SELECT pg_advisory_unlock(hashtextextended(:key, 309))"),
                                {"key": key},
                            )
                    except SQLAlchemyError:
                        conn.invalidate()


def begin_delivery(conn: Connection, job: Job) -> None:
    with conn.begin():
        changed = conn.execute(
            text("""UPDATE jobs SET status='sending'
                WHERE id=:id AND attempts=:attempt AND status='running'"""),
            {"id": job.id, "attempt": job.attempts},
        ).rowcount
        if changed != 1:
            raise RuntimeError("Job attempt cannot submit mail")


def finish_job(
    conn: Connection,
    job: Job,
    now: datetime,
    *,
    succeeded: bool,
    may_retry_delivery: bool,
    max_attempts: int,
) -> None:
    retry_at = now + timedelta(seconds=2 ** min(job.attempts, 8))
    with conn.begin():
        conn.execute(
            text("""UPDATE jobs SET status=CASE
                    WHEN :success THEN 'done'
                    WHEN status='sending' AND NOT :retry_delivery THEN 'uncertain'
                    WHEN attempts>=:max_attempts THEN 'failed'
                    ELSE 'pending' END,
                lease_until=NULL, run_after=:retry_at
                WHERE id=:id AND attempts=:attempt AND status IN ('running','sending')"""),
            {
                "id": job.id,
                "attempt": job.attempts,
                "success": succeeded,
                "retry_delivery": may_retry_delivery,
                "max_attempts": max_attempts,
                "retry_at": retry_at,
            },
        )


def enqueue(
    connection: Connection, tenant_id: UUID, kind: str, payload: dict[str, Any], key: str
) -> UUID:
    row = connection.execute(
        text("""INSERT INTO jobs (id, tenant_id, kind, payload, idempotency_key)
                VALUES (:id, :tenant, :kind, CAST(:payload AS jsonb), :key)
                ON CONFLICT (idempotency_key) DO UPDATE SET idempotency_key = EXCLUDED.idempotency_key
                RETURNING id, tenant_id, kind, payload"""),
        {
            "id": uuid4(),
            "tenant": tenant_id,
            "kind": kind,
            "payload": json.dumps(payload),
            "key": key,
        },
    ).one()
    if row.tenant_id != tenant_id or row.kind != kind or row.payload != payload:
        raise ValueError("Idempotency key already used for a different job")
    return row.id


def drain_once(engine: Engine, handlers: Mapping[str, Callable[[dict[str, Any]], None]]) -> bool:
    if not handlers:
        return False
    with engine.begin() as connection:
        row = connection.execute(
            text("""SELECT id, kind, payload FROM jobs
                    WHERE status = 'pending' AND run_after <= now() AND kind = ANY(:kinds)
                    ORDER BY run_after, id FOR UPDATE SKIP LOCKED LIMIT 1"""),
            {"kinds": list(handlers)},
        ).first()
        if row is None:
            return False
        handler = handlers.get(row.kind)
        if handler is None:
            raise ValueError(f"No handler registered for job kind {row.kind}")
        handler(row.payload)
        connection.execute(
            text("UPDATE jobs SET status = 'done', attempts = attempts + 1 WHERE id = :id"),
            {"id": row.id},
        )
    return True
