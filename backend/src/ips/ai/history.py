"""Build a tenant-scoped, bounded customer context from authoritative records."""

from sqlalchemy import text

from ips.core.ports import KnowledgeSnippet

_PUBLIC_SOURCE = """
    FROM articles a JOIN article_revisions r ON r.tenant_id=a.tenant_id
      AND r.article_id=a.id AND r.id=a.current_revision_id
    JOIN widget_sections ws ON ws.tenant_id=a.tenant_id AND ws.section_id=a.section_id
    JOIN widgets w ON w.tenant_id=ws.tenant_id AND w.id=ws.widget_id
    WHERE a.tenant_id=:tenant AND ws.widget_id=:widget AND w.enabled=true
      AND a.id=:article AND r.version=:revision AND a.visibility='public'
      AND a.status='published'
"""


class ContextUnavailable(Exception):
    """The requested customer context is unavailable."""


class CustomerContext:
    def __init__(self, sessions, search):
        self.sessions = sessions
        self.search = search

    def prepare(self, principal, conversation_id, message_id):
        if principal.conversation_scope and principal.conversation_scope != conversation_id:
            raise ContextUnavailable("context_unavailable")
        params = {
            "tenant": principal.tenant_id,
            "widget": principal.widget_id,
            "customer": principal.customer_id,
            "conversation": conversation_id,
            "message": message_id,
        }
        try:
            with self.sessions() as db:
                message = (
                    db.execute(
                        text("""SELECT m.text,m.seq FROM conversations c
                    JOIN messages m ON m.tenant_id=c.tenant_id AND m.conversation_id=c.id
                    WHERE c.tenant_id=:tenant AND c.id=:conversation AND c.widget_id=:widget
                      AND c.customer_id=:customer AND m.id=:message AND m.kind='customer'"""),
                        params,
                    )
                    .mappings()
                    .first()
                )
                if message is None:
                    raise ContextUnavailable("context_unavailable")
                rows = (
                    db.execute(
                        text("""SELECT kind,text FROM messages
                    WHERE tenant_id=:tenant AND conversation_id=:conversation AND seq<:seq
                      AND kind IN ('customer','assistant','specialist')
                    ORDER BY seq DESC LIMIT 20"""),
                        {**params, "seq": message["seq"]},
                    )
                    .mappings()
                    .all()
                )
                question = message["text"][:8000]
                history = []
                remaining = 16000
                for row in rows:
                    value = row["text"][: min(8000, remaining)]
                    if value:
                        history.append({"kind": row["kind"], "text": value})
                        remaining -= len(value)
                    if not remaining:
                        break
                history.reverse()
            candidates = self.search.search(
                principal.tenant_id, principal.widget_id, question, "customer", limit=5
            )
            return question, history, self._authorize(principal, question, candidates)
        except ContextUnavailable:
            raise
        except Exception:  # noqa: BLE001 - never expose private database/provider details.
            raise ContextUnavailable("context_unavailable") from None

    def _authorize(self, principal, question, candidates):
        if not candidates:
            return []
        authorized = []
        with self.sessions() as db:
            if not db.execute(
                text("SELECT to_regclass('widget_sections') IS NOT NULL")
            ).scalar_one():
                return []
            for candidate in candidates[:5]:
                if not isinstance(candidate, KnowledgeSnippet):
                    continue
                row = (
                    db.execute(
                        text(
                            """SELECT a.id AS article_id,r.version AS revision,r.title,
                    left(ts_headline('simple',r.body,plainto_tsquery('simple',:query),
                         'StartSel="", StopSel="", MaxWords=35, MinWords=15'),2000) AS text
                    """
                            + _PUBLIC_SOURCE
                        ),
                        {
                            "query": question[:4000],
                            "tenant": principal.tenant_id,
                            "widget": principal.widget_id,
                            "article": candidate.article_id,
                            "revision": candidate.revision,
                        },
                    )
                    .mappings()
                    .first()
                )
                if row:
                    snippet = KnowledgeSnippet(**dict(row), visibility="public")
                    if snippet not in authorized:
                        authorized.append(snippet)
        return authorized

    def lock_current_sources(self, db, tenant_id, widget_id, sources):
        """Confirm source access under locks held until the answer is published."""
        params = {"tenant": tenant_id, "widget": widget_id}
        # Widget edits lock the widget before replacing its section bindings.
        widget = db.execute(
            text("""SELECT id FROM widgets
            WHERE tenant_id=:tenant AND id=:widget AND enabled FOR SHARE"""),
            params,
        ).first()
        if widget is None:
            return False
        query = text("SELECT a.id " + _PUBLIC_SOURCE + " FOR SHARE OF a, ws")
        return all(
            db.execute(
                query, {**params, "article": source.article_id, "revision": source.revision}
            ).first()
            is not None
            for source in sources
        )
