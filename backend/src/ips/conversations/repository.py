"""PostgreSQL persistence for tenant-scoped conversations."""

from uuid import uuid4

from sqlalchemy import text


class ConversationRepository:
    def find_customer(self, uow, principal, conversation_id, *, lock=False):
        if principal.conversation_scope and principal.conversation_scope != conversation_id:
            return None
        query = """SELECT * FROM conversations WHERE tenant_id=:tenant AND id=:id
                   AND customer_id=:customer AND widget_id=:widget"""
        if lock:
            query += " FOR UPDATE"
        return (
            uow.execute(
                text(query),
                {
                    "tenant": principal.tenant_id,
                    "id": conversation_id,
                    "customer": principal.customer_id,
                    "widget": principal.widget_id,
                },
            )
            .mappings()
            .first()
        )

    def find_staff(self, uow, tenant_id, conversation_id, *, lock=False):
        query = "SELECT * FROM conversations WHERE tenant_id=:tenant AND id=:id"
        if lock:
            query += " FOR UPDATE"
        return (
            uow.execute(text(query), {"tenant": tenant_id, "id": conversation_id})
            .mappings()
            .first()
        )

    def create(self, uow, principal):
        return (
            uow.execute(
                text("""INSERT INTO conversations (id, tenant_id, widget_id, customer_id)
            VALUES (:id, :tenant, :widget, :customer) RETURNING *"""),
                {
                    "id": uuid4(),
                    "tenant": principal.tenant_id,
                    "widget": principal.widget_id,
                    "customer": principal.customer_id,
                },
            )
            .mappings()
            .one()
        )

    def list_customer(self, uow, principal, after, limit):
        return (
            uow.execute(
                text("""SELECT * FROM conversations WHERE tenant_id=:tenant
            AND customer_id=:customer AND widget_id=:widget AND id>:after
            AND (CAST(:scope AS uuid) IS NULL OR id=:scope) ORDER BY id LIMIT :limit"""),
                {
                    "tenant": principal.tenant_id,
                    "customer": principal.customer_id,
                    "widget": principal.widget_id,
                    "after": after,
                    "scope": principal.conversation_scope,
                    "limit": limit,
                },
            )
            .mappings()
            .all()
        )

    def find_message(self, uow, tenant_id, conversation_id, client_message_id):
        return (
            uow.execute(
                text("""SELECT *, '[]'::jsonb AS attachment_ids FROM messages
            WHERE tenant_id=:tenant AND conversation_id=:conversation AND client_message_id=:key"""),
                {
                    "tenant": tenant_id,
                    "conversation": conversation_id,
                    "key": client_message_id,
                },
            )
            .mappings()
            .first()
        )

    def append_message(
        self, uow, tenant_id, conversation_id, kind, text_value, client_message_id, author_id=None
    ):
        # Caller holds the conversation row lock, serializing seq allocation.
        row = (
            uow.execute(
                text("""INSERT INTO messages
            (id, tenant_id, conversation_id, seq, kind, author_id, text, client_message_id)
            SELECT :id, :tenant, :conversation, COALESCE(MAX(seq), 0)+1,
                   :kind, :author, :content, :key
            FROM messages WHERE tenant_id=:tenant AND conversation_id=:conversation
            RETURNING *, '[]'::jsonb AS attachment_ids"""),
                {
                    "id": uuid4(),
                    "tenant": tenant_id,
                    "conversation": conversation_id,
                    "kind": kind,
                    "author": author_id,
                    "content": text_value,
                    "key": client_message_id,
                },
            )
            .mappings()
            .one()
        )
        uow.execute(
            text("UPDATE conversations SET updated_at=now() WHERE tenant_id=:tenant AND id=:id"),
            {"tenant": tenant_id, "id": conversation_id},
        )
        return row

    def create_ai_run(self, uow, tenant_id, conversation_id, message_id, version):
        uow.execute(
            text("""INSERT INTO ai_runs
            (id, tenant_id, conversation_id, input_message_id, generation_version)
            VALUES (:id, :tenant, :conversation, :message, :version)"""),
            {
                "id": uuid4(),
                "tenant": tenant_id,
                "conversation": conversation_id,
                "message": message_id,
                "version": version,
            },
        )

    def list_messages(self, uow, tenant_id, conversation_id, after_seq, limit, *, public):
        visibility = "AND kind IN ('customer','assistant','specialist')" if public else ""
        return (
            uow.execute(
                text(
                    """SELECT *, '[]'::jsonb AS attachment_ids FROM messages
            WHERE tenant_id=:tenant AND conversation_id=:conversation AND seq>:after """
                    + visibility
                    + " ORDER BY seq ASC LIMIT :limit"
                ),
                {
                    "tenant": tenant_id,
                    "conversation": conversation_id,
                    "after": after_seq,
                    "limit": limit,
                },
            )
            .mappings()
            .all()
        )

    def mark_human(self, uow, tenant_id, conversation_id):
        uow.execute(
            text("""UPDATE conversations SET mode='human', version=version+1,
            updated_at=now() WHERE tenant_id=:tenant AND id=:id AND mode<>'human'"""),
            {"tenant": tenant_id, "id": conversation_id},
        )
