"""Deliver invitations and conversation notifications through shared SMTP settings."""

import base64
import hashlib
import hmac
import smtplib
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Literal
from urllib.parse import urlencode
from uuid import UUID

from sqlalchemy import Engine, text

from ips.core.config import Settings
from ips.platform.email_links import link_token
from ips.platform.widget_api import token_hash


class SMTPDeliveryError(RuntimeError):
    pass


def send_mail(settings: Settings, message: EmailMessage) -> None:
    address = (settings.smtp_host, settings.smtp_port)
    timeout = settings.smtp_timeout_seconds
    try:
        if settings.smtp_tls == "implicit":
            smtp = smtplib.SMTP_SSL(*address, timeout=timeout, context=ssl.create_default_context())
        else:
            smtp = smtplib.SMTP(*address, timeout=timeout)
        with smtp:
            if settings.smtp_tls == "starttls":
                smtp.starttls(context=ssl.create_default_context())
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException):
        # SMTP replies may contain addresses, message content, or credentials.
        raise SMTPDeliveryError("SMTP delivery failed") from None


def mail_message(settings: Settings, recipient: str, subject: str, body: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    return message


def invitation_token(secret: str, invitation_id: UUID) -> str:
    signature = hmac.new(
        secret.encode(), b"ips:invitation:v1:" + invitation_id.bytes, hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(invitation_id.bytes + signature).rstrip(b"=").decode("ascii")


def invitation_handler(
    engine: Engine, settings: Settings, send: Callable[[str, str], None] | None = None
):
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
        if send is not None:
            send(row.email, url)
        else:
            send_mail(settings, invitation_mail(settings, row.email, url))

    return handle


def invitation_mail(settings: Settings, recipient: str, url: str) -> EmailMessage:
    return mail_message(
        settings, recipient, "Invitation to IPS support", f"Accept your invitation: {url}"
    )


@dataclass(frozen=True)
class ContinuationMail:
    recipient: str
    purpose: Literal["confirm", "exchange"]
    token: str


def confirmation_mail(engine: Engine, settings: Settings, payload: dict) -> ContinuationMail | None:
    link_id = UUID(payload["link_id"])
    token = link_token(settings.session_secret, "confirm", link_id)
    with engine.connect() as conn:
        row = conn.execute(
            text("""SELECT l.email FROM customer_links l
            JOIN conversations c ON c.tenant_id=l.tenant_id AND c.id=l.conversation_id
            JOIN widgets w ON w.tenant_id=c.tenant_id AND w.id=c.widget_id
            WHERE l.id=:id AND l.tenant_id=:tenant AND l.token_hash=:hash
              AND l.purpose='confirm' AND l.consumed_at IS NULL AND l.expires_at > now()
              AND c.customer_id=l.customer_id AND w.enabled AND l.site_origin=ANY(w.allowed_origins)"""),
            {"id": link_id, "tenant": UUID(payload["tenant_id"]), "hash": token_hash(token)},
        ).first()
    return ContinuationMail(row.email, "confirm", token) if row is not None else None


def staff_reply_mail(engine: Engine, settings: Settings, payload: dict) -> ContinuationMail | None:
    # The same event must reuse the same grant after an SMTP error.
    message_id = UUID(payload["message_id"])
    token = link_token(settings.session_secret, "exchange", message_id)
    params = {
        "id": message_id,
        "tenant": UUID(payload["tenant_id"]),
        "conversation": UUID(payload["conversation_id"]),
        "hash": token_hash(token),
    }
    with engine.begin() as conn:
        conn.execute(
            text("""INSERT INTO customer_links
                (id, tenant_id, customer_id, conversation_id, purpose, confirmation_id, site_origin, email, token_hash)
            SELECT m.id, m.tenant_id, v.customer_id, m.conversation_id,
                'exchange', v.id, v.site_origin, v.email, :hash
            FROM messages m
            JOIN conversations c ON c.tenant_id=m.tenant_id AND c.id=m.conversation_id
            JOIN widgets w ON w.tenant_id=c.tenant_id AND w.id=c.widget_id
            CROSS JOIN LATERAL (
                SELECT id, customer_id, site_origin, email, consumed_at FROM customer_links
                WHERE tenant_id=m.tenant_id AND conversation_id=m.conversation_id
                  AND purpose='confirm' AND consumed_at IS NOT NULL
                ORDER BY consumed_at DESC, id DESC LIMIT 1
            ) v
            WHERE m.tenant_id=:tenant AND m.conversation_id=:conversation AND m.id=:id
                AND m.kind='specialist' AND m.created_at >= v.consumed_at
                AND c.customer_id=v.customer_id AND w.enabled AND v.site_origin=ANY(w.allowed_origins)
            ON CONFLICT (id) DO NOTHING"""),
            params,
        )
        row = conn.execute(
            text("""SELECT l.email FROM customer_links l
            JOIN conversations c ON c.tenant_id=l.tenant_id AND c.id=l.conversation_id
            JOIN widgets w ON w.tenant_id=c.tenant_id AND w.id=c.widget_id
            WHERE l.id=:id AND l.tenant_id=:tenant AND l.conversation_id=:conversation
                AND l.purpose='exchange' AND l.token_hash=:hash
                AND l.consumed_at IS NULL AND l.expires_at > now()
                AND c.customer_id=l.customer_id AND w.enabled AND l.site_origin=ANY(w.allowed_origins)
                AND l.confirmation_id=(
                    SELECT v.id FROM customer_links v
                    WHERE v.tenant_id=l.tenant_id AND v.conversation_id=l.conversation_id
                        AND v.purpose='confirm' AND v.consumed_at IS NOT NULL
                    ORDER BY v.consumed_at DESC, v.id DESC LIMIT 1
                )"""),
            params,
        ).first()
    return ContinuationMail(row.email, "exchange", token) if row is not None else None


def notification_handlers(
    engine: Engine, settings: Settings, send: Callable[[EmailMessage], None] | None = None
) -> dict[str, Callable[[dict], None]]:
    def deliver(message: EmailMessage) -> None:
        if send is not None:
            send(message)
        else:
            send_mail(settings, message)

    def deliver_link(mail: ContinuationMail | None) -> None:
        if mail is None:
            return
        fragment = urlencode({"token": mail.token, "action": mail.purpose})
        url = f"{settings.widget_origin.rstrip('/')}/continuation.html#{fragment}"
        subject = (
            "Confirm your support email" if mail.purpose == "confirm" else "New reply from support"
        )
        deliver(mail_message(settings, mail.recipient, subject, f"{subject}: {url}"))

    def handle_confirmation(payload: dict) -> None:
        deliver_link(confirmation_mail(engine, settings, payload))

    def handle_reply(payload: dict) -> None:
        deliver_link(staff_reply_mail(engine, settings, payload))

    return {
        "invitation": invitation_handler(
            engine,
            settings,
            lambda recipient, url: deliver(invitation_mail(settings, recipient, url)),
        ),
        "customer_email": handle_confirmation,
        "staff_reply": handle_reply,
    }


def main() -> None:
    from sqlalchemy import create_engine

    from ips.core.jobs import drain_once

    settings = Settings()
    engine = create_engine(settings.database_url)
    try:
        drain_once(engine, notification_handlers(engine, settings))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
