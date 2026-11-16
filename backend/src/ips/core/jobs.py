"""Small transactional job queue for the single-worker prototype."""

import json
from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine, text


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
    with engine.begin() as connection:
        row = connection.execute(
            text("""SELECT id, kind, payload FROM jobs
                    WHERE status = 'pending' AND run_after <= now()
                    ORDER BY run_after, id FOR UPDATE SKIP LOCKED LIMIT 1""")
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
