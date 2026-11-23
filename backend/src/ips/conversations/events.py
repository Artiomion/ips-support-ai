"""Transactional public staff replies and notification events."""

from ips.core.jobs import enqueue
from ips.core.ports import StaffReplyEvent


class QueuedStaffReplyNotifications:
    """Persist an event for B07; no email is sent in this adapter."""

    def enqueue_staff_reply(self, uow, event):
        enqueue(
            uow.connection(),
            event.tenant_id,
            "staff_reply",
            {
                "conversation_id": str(event.conversation_id),
                "message_id": str(event.message_id),
                "created_at": event.created_at.isoformat(),
            },
            f"staff_reply:{event.message_id}",
        )


class StaffReplyService:
    def __init__(self, conversations, tickets, notifications):
        self.conversations = conversations
        self.tickets = tickets
        self.notifications = notifications

    def send(self, db, ctx, conversation_id, text_value, internal, client_message_id):
        # Hold the same conversation lock through idempotency check and all side effects.
        self.conversations.lock_for_handoff(db, ctx.tenant_id, conversation_id)
        existing = self.conversations.repository.find_message(
            db, ctx.tenant_id, conversation_id, client_message_id
        )
        message = self.conversations.add_staff_message(
            db, ctx, conversation_id, text_value, internal, client_message_id
        )
        created = existing is None
        if created and not internal:
            self.tickets.record_staff_reply(db, ctx, conversation_id, message.created_at)
            self.notifications.enqueue_staff_reply(
                db,
                StaffReplyEvent(
                    tenant_id=ctx.tenant_id,
                    conversation_id=conversation_id,
                    message_id=message.id,
                    created_at=message.created_at,
                ),
            )
        return message, created
