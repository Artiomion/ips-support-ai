"""Exercise the real SMTP adapter against an isolated local server."""

import email
import queue
import smtplib
import socketserver
import ssl
import threading
import traceback
from contextlib import contextmanager
from email.message import EmailMessage
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text

from ips.core.config import Settings
from ips.core.jobs import drain_once, enqueue
from ips.main import create_app
from ips.platform.notifications import SMTPDeliveryError, invitation_handler, send_mail


class SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b"220 localhost test ESMTP\r\n")
        while line := self.rfile.readline():
            command = line.split(None, 1)[0].upper()
            if command in (b"EHLO", b"HELO"):
                self.wfile.write(b"250-localhost\r\n250 8BITMIME\r\n")
            elif command in (b"MAIL", b"RCPT", b"RSET", b"NOOP"):
                self.wfile.write(b"250 OK\r\n")
            elif command == b"DATA":
                self.wfile.write(b"354 End data with <CR><LF>.<CR><LF>\r\n")
                chunks = []
                while True:
                    part = self.rfile.readline()
                    if part in (b".\r\n", b""):
                        break
                    chunks.append(part)
                self.server.messages.put(b"".join(chunks))
                self.wfile.write(b"250 Message accepted\r\n")
            elif command == b"QUIT":
                self.wfile.write(b"221 Bye\r\n")
                break
            else:
                self.wfile.write(b"502 Not supported\r\n")


class SMTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        self.messages = queue.Queue()
        super().__init__(("127.0.0.1", 0), SMTPHandler)


@contextmanager
def smtp_inbox():
    with SMTPServer() as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_invitation_is_delivered_to_local_smtp_server():
    with smtp_inbox() as server:
        settings = Settings(
            web_origin="http://localhost:5173",
            smtp_host="127.0.0.1",
            smtp_port=server.server_address[1],
        )
        app = create_app(settings)
        recipient = f"{uuid4()}@example.org"
        with TestClient(app) as client:
            admin = client.post(
                "/api/v1/auth/register",
                headers={"Origin": "http://localhost:5173"},
                json={
                    "organization_name": "A",
                    "email": f"{uuid4()}@example.org",
                    "password": "correct-horse-battery",
                },
            ).json()
            result = client.post(
                "/api/v1/members/invitations",
                headers={
                    "Origin": "http://localhost:5173",
                    "X-CSRF-Token": admin["csrf_token"],
                },
                json={"email": recipient, "role": "specialist"},
            )
            assert result.status_code == 201
            # Other tests can leave accepted or expired jobs in the queue.
            # Drain them as a worker would until this message is delivered.
            message = None
            handler = invitation_handler(app.state.engine, settings)
            for _ in range(20):
                if not drain_once(app.state.engine, {"invitation": handler}):
                    break
                while not server.messages.empty():
                    candidate = email.message_from_bytes(server.messages.get_nowait())
                    if candidate["To"] == recipient:
                        message = candidate
                if message is not None:
                    break
            assert message is not None
            assert "/invite#token=" in message.get_payload(decode=True).decode()


@pytest.mark.parametrize("tls", ["starttls", "implicit"])
def test_smtp_auth_uses_verified_tls_before_sending(monkeypatch, tls):
    calls = []

    def verify(context):
        assert context.check_hostname
        assert context.verify_mode == ssl.CERT_REQUIRED
        calls.append("tls")

    class Transport:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def starttls(self, *, context):
            verify(context)

        def login(self, username, password):
            assert (username, password) == ("test-account", "synthetic-password")
            calls.append("login")

        def send_message(self, _message):
            calls.append("send")

    def connect(host, port, *, timeout, context=None):
        assert (host, port, timeout) == ("smtp.example.org", 2465, 3)
        if context is not None:
            verify(context)
        return Transport()

    transport = "SMTP_SSL" if tls == "implicit" else "SMTP"
    monkeypatch.setattr(smtplib, transport, connect)
    settings = Settings(
        smtp_host="smtp.example.org",
        smtp_port=2465,
        smtp_tls=tls,
        smtp_username="test-account",
        smtp_password="synthetic-password",
        smtp_timeout_seconds=3,
    )
    send_mail(settings, EmailMessage())
    assert calls == ["tls", "login", "send"]


def test_cleartext_smtp_auth_is_rejected():
    password = "synthetic-password"
    with pytest.raises(ValidationError, match="SMTP credentials require TLS") as failure:
        Settings(smtp_tls="none", smtp_username="test", smtp_password=password)
    assert password not in str(failure.value)


def test_smtp_rejection_keeps_job_pending_and_redacts_reply(monkeypatch, caplog):
    detail = "synthetic-private-smtp-reply"

    def rejected(*args, **kwargs):
        raise smtplib.SMTPAuthenticationError(535, detail.encode())

    monkeypatch.setattr(smtplib, "SMTP_SSL", rejected)
    settings = Settings(smtp_tls="implicit")
    app = create_app(settings)
    with TestClient(app) as client:
        registration = client.post(
            "/api/v1/auth/register",
            headers={"Origin": settings.web_origin},
            json={
                "organization_name": "SMTP retry",
                "email": f"{uuid4()}@example.org",
                "password": "correct-horse-battery",
            },
        )
        assert registration.status_code == 201
        engine = app.state.engine
        kind = f"smtp-test:{uuid4()}"
        with engine.begin() as conn:
            job = enqueue(conn, UUID(registration.json()["tenant_id"]), kind, {}, kind)
        with pytest.raises(SMTPDeliveryError, match="SMTP delivery failed") as failure:
            drain_once(engine, {kind: lambda _: send_mail(settings, EmailMessage())})
        assert detail not in "".join(traceback.format_exception(failure.value))
        assert detail not in caplog.text
        with engine.connect() as conn:
            status, attempts = conn.execute(
                text("SELECT status, attempts FROM jobs WHERE id=:id"), {"id": job}
            ).one()
        assert (status, attempts) == ("pending", 0)
