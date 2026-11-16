"""Invitation delivery for the development SMTP sandbox."""

import base64
import hashlib
import hmac
import smtplib
from collections.abc import Callable
from email.message import EmailMessage
from uuid import UUID

from sqlalchemy import Engine, text

from ips.core.config import Settings


def invitation_token(secret: str, invitation_id: UUID) -> str:
    signature = hmac.new(
        secret.encode(), b"ips:invitation:v1:" + invitation_id.bytes, hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(invitation_id.bytes + signature).rstrip(b"=").decode("ascii")


def invitation_handler(
    engine: Engine, settings: Settings, send: Callable[[str, str], None] | None = None
):
    def smtp_send(recipient: str, url: str) -> None:
        message = EmailMessage()
        message["From"] = settings.smtp_from
        message["To"] = recipient
        message["Subject"] = "Invitation to IPS support"
        message.set_content(f"Accept your invitation: {url}")
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            smtp.send_message(message)

    def handle(payload: dict) -> None:
        invitation_id, tenant_id = UUID(payload["invitation_id"]), UUID(payload["tenant_id"])
        with engine.connect() as conn:
            row = conn.execute(
                text("""SELECT email FROM invitations WHERE id=:id AND tenant_id=:tenant
                AND accepted_at IS NULL AND expires_at > now()"""),
                {"id": invitation_id, "tenant": tenant_id},
            ).first()
        if row is None:
            return
        url = (
            settings.web_origin.rstrip("/")
            + "/invite#token="
            + invitation_token(settings.session_secret, invitation_id)
        )
        (send or smtp_send)(row.email, url)

    return handle


def main() -> None:
    from sqlalchemy import create_engine

    from ips.core.jobs import drain_once

    settings = Settings()
    engine = create_engine(settings.database_url)
    try:
        drain_once(engine, {"invitation": invitation_handler(engine, settings)})
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
