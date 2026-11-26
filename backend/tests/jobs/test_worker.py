import smtplib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from ips.core.config import Settings
from ips.core.jobs import enqueue
from ips.core.worker import Worker
from ips.platform.notifications import send_mail


@pytest.fixture
def queue():
    engine = create_engine(Settings().database_url)
    tenant = uuid4()
    kind = f"test:{tenant}"
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO organizations (id, name) VALUES (:id,'test')"), {"id": tenant}
        )
        job = enqueue(conn, tenant, kind, {"value": 1}, kind)
    try:
        yield engine, job, tenant, kind
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM jobs WHERE tenant_id=:id"), {"id": tenant})
            conn.execute(text("DELETE FROM organizations WHERE id=:id"), {"id": tenant})
        engine.dispose()


def status(engine, job):
    with engine.connect() as conn:
        return tuple(
            conn.execute(
                text("SELECT status, attempts, lease_until FROM jobs WHERE id=:id"), {"id": job}
            ).one()
        )


def test_two_workers_cannot_run_a_live_attempt_even_after_lease_expires(queue):
    engine, job, tenant, kind = queue
    now = datetime.now(UTC)
    entered, release = Event(), Event()
    calls = []

    def handle(item, begin_delivery):
        calls.append((item.tenant_id, item.payload))
        entered.set()
        assert release.wait(5)

    first = Worker(engine, {kind: handle}, clock=lambda: now)
    second = Worker(engine, {kind: handle}, clock=lambda: now + timedelta(minutes=5))
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(first.run_once)
        try:
            assert entered.wait(5)
            assert status(engine, job)[0] == "running"
            assert not second.run_once()
        finally:
            release.set()
        assert running.result(timeout=5)
    assert calls == [(tenant, {"value": 1})]
    assert status(engine, job) == ("done", 1, None)


@pytest.mark.parametrize("submitted", [False, True])
def test_crash_retries_unsubmitted_job_and_prevents_duplicate_send(queue, submitted):
    engine, job, _, kind = queue
    now = datetime.now(UTC)
    sent = []

    def crash(item, begin_delivery):
        if submitted:
            begin_delivery()
            sent.append(item.id)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        Worker(engine, {kind: crash}, clock=lambda: now).run_once()

    def send(item, begin_delivery):
        begin_delivery()
        sent.append(item.id)

    worker = Worker(engine, {kind: send}, clock=lambda: now + timedelta(minutes=5))
    worker.run_once()
    assert sent == [job]
    assert status(engine, job) == (("uncertain", 1, None) if submitted else ("done", 2, None))


def test_retry_waits_for_backoff_and_stops_after_attempt_limit(queue):
    engine, job, _, kind = queue
    now = datetime.now(UTC)

    def fails(item, begin_delivery):
        raise RuntimeError("synthetic failure")

    worker = Worker(engine, {kind: fails}, clock=lambda: now, max_attempts=2)
    assert worker.run_once()
    assert status(engine, job) == ("pending", 1, None)
    assert not worker.run_once()
    now += timedelta(seconds=5)
    assert worker.run_once()
    assert status(engine, job) == ("failed", 2, None)


@pytest.mark.parametrize(
    ("smtp_failure", "expected"),
    [
        (smtplib.SMTPDataError(450, b"SMTP refused the message"), "pending"),
        (smtplib.SMTPServerDisconnected("Lost connection while sending"), "uncertain"),
        (None, "done"),
    ],
)
def test_smtp_refusal_or_disconnect_is_classified_without_duplicating_mail(
    queue, monkeypatch, smtp_failure, expected
):
    engine, job, _, kind = queue

    class Transport:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, *args):
            if exc_type is None:
                raise smtplib.SMTPServerDisconnected("Lost connection during QUIT")

        def send_message(self, message):
            assert status(engine, job)[0] == "sending"
            if smtp_failure is not None:
                raise smtp_failure

    def handler(item, begin_delivery):
        settings = Settings(smtp_tls="none", smtp_username="", smtp_password="")
        send_mail(settings, EmailMessage(), before_send=begin_delivery)

    monkeypatch.setattr(smtplib, "SMTP", Transport)
    assert Worker(engine, {kind: handler}).run_once()
    assert status(engine, job) == (expected, 1, None)
