"""Conversation operations; the caller owns the transaction."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text as sql_text

from ips.conversations.schemas import PublicMessage
from ips.core.ports import ConversationState, CustomerPrincipal, StaffMessage


class ConversationService:
    def __init__(self, repository):
        self.repository = repository

    def _customer_conversation(self, uow, principal, conversation_id, *, lock=False):
        row = self.repository.find_customer(uow, principal, conversation_id, lock=lock)
        if row is None:
            raise HTTPException(404, "Conversation unavailable")
        return row

    def send_customer(
        self,
        uow,
        principal: CustomerPrincipal,
        conversation_id: UUID,
        client_message_id: UUID,
        text: str,
        attachment_ids: list[UUID] | None = None,
    ):
        # The conversation lock serializes retries, sequence allocation and handoff.
        conversation = self._customer_conversation(uow, principal, conversation_id, lock=True)
        attachment_ids = attachment_ids or []
        if len(attachment_ids) > 4 or len(set(attachment_ids)) != len(attachment_ids):
            raise HTTPException(422, "Invalid attachments")
        existing = self.repository.find_message(
            uow, principal.tenant_id, conversation_id, client_message_id
        )
        if existing is not None:
            if (
                existing["kind"] != "customer"
                or existing["text"] != text
                or [str(value) for value in existing["attachment_ids"]]
                != [str(value) for value in attachment_ids]
            ):
                raise HTTPException(409, "Message id already used")
            return self._public_message(existing), False
        if attachment_ids:
            found = (
                uow.execute(
                    sql_text("""SELECT id FROM attachments WHERE tenant_id=:tenant
                AND conversation_id=:conversation AND customer_id=:customer AND widget_id=:widget
                AND state='ready' AND id=ANY(CAST(:ids AS uuid[]))"""),
                    {
                        "tenant": principal.tenant_id,
                        "conversation": conversation_id,
                        "customer": principal.customer_id,
                        "widget": principal.widget_id,
                        "ids": attachment_ids,
                    },
                )
                .scalars()
                .all()
            )
            if len(found) != len(attachment_ids):
                raise HTTPException(404, "Attachment unavailable")
        row = self.repository.append_message(
            uow, principal.tenant_id, conversation_id, "customer", text, client_message_id
        )
        for position, attachment_id in enumerate(attachment_ids):
            uow.execute(
                sql_text("""INSERT INTO message_attachments
                (tenant_id,conversation_id,message_id,attachment_id,position)
                VALUES (:tenant,:conversation,:message,:attachment,:position)"""),
                {
                    "tenant": principal.tenant_id,
                    "conversation": conversation_id,
                    "message": row["id"],
                    "attachment": attachment_id,
                    "position": position,
                },
            )
        if conversation["mode"] == "bot":
            self.repository.create_ai_run(
                uow, principal.tenant_id, conversation_id, row["id"], conversation["version"]
            )
        if attachment_ids:
            row = self.repository.find_message(
                uow, principal.tenant_id, conversation_id, client_message_id
            )
        return self._public_message(row), True

    def customer_messages(self, uow, principal, conversation_id, after_seq, limit):
        self._customer_conversation(uow, principal, conversation_id)
        rows = self.repository.list_messages(
            uow, principal.tenant_id, conversation_id, after_seq, limit, public=True
        )
        return [self._public_message(row) for row in rows]

    def lock_for_handoff(self, uow, tenant_id, conversation_id):
        row = self.repository.find_staff(uow, tenant_id, conversation_id, lock=True)
        if row is None:
            raise HTTPException(404, "Conversation unavailable")
        return ConversationState.model_validate(
            {key: row[key] for key in ConversationState.model_fields}
        )

    def mark_human(self, uow, state):
        self.repository.mark_human(uow, state.tenant_id, state.id)

    def add_staff_message(self, uow, ctx, conversation_id, text, internal, client_message_id):
        row = self.repository.find_staff(uow, ctx.tenant_id, conversation_id, lock=True)
        if row is None:
            raise HTTPException(404, "Conversation unavailable")
        kind = "internal_note" if internal else "specialist"
        existing = self.repository.find_message(
            uow, ctx.tenant_id, conversation_id, client_message_id
        )
        if existing is not None:
            if (
                existing["kind"] != kind
                or existing["text"] != text
                or existing["author_id"] != ctx.user_id
            ):
                raise HTTPException(409, "Message id already used")
            return self._staff_message(existing)
        created = self.repository.append_message(
            uow,
            ctx.tenant_id,
            conversation_id,
            kind,
            text,
            client_message_id,
            author_id=ctx.user_id,
        )
        return self._staff_message(created)

    def staff_messages(self, uow, ctx, conversation_id, after_seq, limit):
        if self.repository.find_staff(uow, ctx.tenant_id, conversation_id) is None:
            raise HTTPException(404, "Conversation unavailable")
        rows = self.repository.list_messages(
            uow, ctx.tenant_id, conversation_id, after_seq, limit, public=False
        )
        return [self._staff_message(row) for row in rows]

    @staticmethod
    def _staff_message(row):
        return StaffMessage.model_validate({key: row[key] for key in StaffMessage.model_fields})

    @staticmethod
    def _public_message(row):
        # Select fields explicitly, so internal metadata cannot appear in public responses.
        return PublicMessage.model_validate({key: row[key] for key in PublicMessage.model_fields})
