"""Exercise the real SMTP adapter against an isolated local server."""

import email
import queue
import socketserver
import threading
from uuid import uuid4

from fastapi.testclient import TestClient

from ips.core.config import Settings
from ips.core.jobs import drain_once
from ips.main import create_app
from ips.platform.notifications import invitation_handler


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


def test_invitation_is_delivered_to_local_smtp_server():
    with SMTPServer() as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
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
        finally:
            server.shutdown()
            thread.join(timeout=5)
