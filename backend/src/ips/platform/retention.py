"""Remove expired conversation assets and import links within one tenant."""

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import Connection, text

INACTIVE_CONVERSATION = """
    c.updated_at<:cutoff
    AND NOT EXISTS (
        SELECT 1 FROM tickets t WHERE t.tenant_id=c.tenant_id
            AND t.conversation_id=c.id AND t.status<>'closed'
    )
    AND NOT EXISTS (
        SELECT 1 FROM ai_runs r WHERE r.tenant_id=c.tenant_id
            AND r.conversation_id=c.id AND r.state IN ('pending','running')
    )
"""


def purge_expired(
    conn: Connection, tenant_id: UUID, now: datetime, days: int, *, limit: int = 100
) -> None:
    try:
        cutoff = now - timedelta(days=days)
    except OverflowError:
        return
    params = {"tenant": tenant_id, "cutoff": cutoff, "limit": limit}
    conn.execute(
        text(f"""WITH old AS (
            SELECT m.id FROM messages m
            JOIN conversations c ON c.tenant_id=m.tenant_id AND c.id=m.conversation_id
            WHERE m.tenant_id=:tenant AND m.created_at<:cutoff AND m.sources<>'[]'::jsonb
                AND {INACTIVE_CONVERSATION}
            ORDER BY m.created_at,m.id LIMIT :limit FOR UPDATE OF c,m SKIP LOCKED
        )
        UPDATE messages m SET sources='[]'::jsonb
        FROM old WHERE m.tenant_id=:tenant AND m.id=old.id"""),
        params,
    )
    # Older deployments can run retention before the C09 migration is installed.
    if conn.scalar(
        text("""SELECT to_regclass('attachments') IS NOT NULL
        AND to_regclass('message_attachments') IS NOT NULL""")
    ):
        remove_attachments(conn, params)
    if conn.scalar(text("SELECT to_regclass('knowledge_import_articles') IS NOT NULL")):
        conn.execute(
            text("""WITH old AS (
                SELECT l.import_id,l.position FROM knowledge_import_articles l
                JOIN knowledge_imports i ON i.tenant_id=l.tenant_id AND i.id=l.import_id
                WHERE l.tenant_id=:tenant AND i.created_at<:cutoff AND i.state IN ('ready','failed')
                ORDER BY i.created_at,l.import_id,l.position LIMIT :limit
                FOR UPDATE OF i,l SKIP LOCKED
            )
            DELETE FROM knowledge_import_articles l USING old
            WHERE l.tenant_id=:tenant AND l.import_id=old.import_id AND l.position=old.position"""),
            params,
        )


def remove_attachments(conn: Connection, params: dict) -> None:
    attachments = (
        conn.execute(
            text(f"""SELECT a.id FROM attachments a
        JOIN conversations c ON c.tenant_id=a.tenant_id AND c.id=a.conversation_id
        WHERE a.tenant_id=:tenant AND a.created_at<:cutoff AND {INACTIVE_CONVERSATION}
        ORDER BY a.created_at,a.id LIMIT :limit FOR UPDATE OF c,a SKIP LOCKED"""),
            params,
        )
        .scalars()
        .all()
    )
    if not attachments:
        return
    ids = {"tenant": params["tenant"], "ids": attachments}
    conn.execute(
        text("DELETE FROM message_attachments WHERE tenant_id=:tenant AND attachment_id=ANY(:ids)"),
        ids,
    )
    conn.execute(
        text("DELETE FROM attachments WHERE tenant_id=:tenant AND id=ANY(:ids)"),
        ids,
    )
