"""Run the persisted job queue with `python -m ips.core.worker`."""

import logging
import signal
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from threading import Event

from fastapi import HTTPException
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from ips.core.config import Settings
from ips.core.jobs import Job, begin_delivery, finish_job, lease_job
from ips.platform.notifications import SMTPDeliveryError, notification_handlers, send_mail

logger = logging.getLogger(__name__)
Handler = Callable[[Job, Callable[[], None]], None]


def utc_now() -> datetime:
    return datetime.now(UTC)


class Worker:
    def __init__(
        self,
        engine: Engine,
        handlers: Mapping[str, Handler],
        *,
        clock: Callable[[], datetime] = utc_now,
        lease: timedelta = timedelta(minutes=2),
        max_attempts: int = 5,
    ):
        if lease <= timedelta() or max_attempts < 1:
            raise ValueError("A job requires a positive lease and attempt limit")
        self.engine = engine
        self.handlers = handlers
        self.clock = clock
        self.lease = lease
        self.max_attempts = max_attempts

    def run_once(self, *, raise_on_error: bool = False) -> bool:
        if not self.handlers:
            return False
        with lease_job(
            self.engine, list(self.handlers), self.clock(), self.lease, self.max_attempts
        ) as claimed:
            if claimed is None:
                return False
            conn, job = claimed
            try:
                self.handlers[job.kind](job, lambda: begin_delivery(conn, job))
            except Exception as error:
                finish_job(
                    conn,
                    job,
                    self.clock(),
                    succeeded=False,
                    may_retry_delivery=isinstance(error, SMTPDeliveryError) and error.retryable,
                    max_attempts=self.max_attempts,
                )
                if raise_on_error:
                    raise
                logger.warning("Job %s failed on attempt %s", job.id, job.attempts)
            else:
                finish_job(
                    conn,
                    job,
                    self.clock(),
                    succeeded=True,
                    may_retry_delivery=False,
                    max_attempts=self.max_attempts,
                )
            return True

    def run_forever(self, stop: Event) -> None:
        next_maintenance = self.clock()
        while not stop.is_set():
            try:
                now = self.clock()
                if now >= next_maintenance:
                    run_maintenance(self.engine, now)
                    next_maintenance = now + timedelta(minutes=1)
                if self.run_once():
                    continue
            except SQLAlchemyError:
                logger.warning("Worker could not reach or update PostgreSQL")
            stop.wait(1)


def mail_handlers(engine: Engine, settings: Settings) -> dict[str, Handler]:
    def make_handler(kind: str) -> Handler:
        def handle(job: Job, mark_sending: Callable[[], None]) -> None:
            handlers = notification_handlers(
                engine,
                settings,
                lambda message: send_mail(settings, message, before_send=mark_sending),
            )
            handlers[kind]({**job.payload, "tenant_id": str(job.tenant_id)})

        return handle

    return {kind: make_handler(kind) for kind in ("invitation", "customer_email", "staff_reply")}


def run_maintenance(engine: Engine, now: datetime) -> None:
    from ips.platform.retention import purge_expired
    from ips.platform.settings import get_limits
    from ips.tickets.auto_close import close_due_tickets

    with engine.connect() as conn:
        tenants = conn.execute(text("SELECT id FROM organizations")).scalars().all()
    for tenant_id in tenants:
        try:
            with engine.begin() as conn:
                locked = conn.scalar(
                    text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 310))"),
                    {"key": str(tenant_id)},
                )
                if locked:
                    policy = get_limits(conn, tenant_id, lock=True)
                    close_due_tickets(conn, tenant_id, now, policy.auto_close_hours)
                    purge_expired(conn, tenant_id, now, policy.retention_days)
        except (HTTPException, SQLAlchemyError, ValueError):
            logger.warning("Organization maintenance failed")


def main() -> None:
    settings = Settings()
    engine = create_engine(
        settings.database_url, pool_pre_ping=True, connect_args={"connect_timeout": 2}
    )
    stop = Event()
    for interrupt in (signal.SIGINT, signal.SIGTERM):
        signal.signal(interrupt, lambda *_: stop.set())
    try:
        Worker(engine, mail_handlers(engine, settings)).run_forever(stop)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
