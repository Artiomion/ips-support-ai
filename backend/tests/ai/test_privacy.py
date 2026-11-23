from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ips.ai.history import ContextUnavailable, CustomerContext
from ips.conversations.repository import ConversationRepository
from ips.conversations.service import ConversationService
from ips.core.config import Settings
from ips.core.ports import CustomerPrincipal, KnowledgeSnippet
from ips.knowledge.repository import KnowledgeRepository
from ips.knowledge.schemas import ArticleInput


def test_customer_context_excludes_other_tenants_notes_and_other_conversations():
    engine = create_engine(Settings().database_url, connect_args={"connect_timeout": 2})
    tenant, other, widget, customer = (uuid4() for _ in range(4))
    principal = CustomerPrincipal(tenant_id=tenant, customer_id=customer, widget_id=widget)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            db = Session(bind=connection)
            for tid in (tenant, other):
                db.execute(
                    text("INSERT INTO organizations (id,name) VALUES (:id,'privacy')"), {"id": tid}
                )
            db.execute(
                text("""INSERT INTO widgets (id,tenant_id,name,public_key,color,greeting,allowed_origins)
                VALUES (:id,:tenant,'test',:key,'#ffffff','hello',ARRAY['https://example.test'])"""),
                {"id": widget, "tenant": tenant, "key": str(widget)},
            )
            db.execute(
                text("INSERT INTO customers (id,tenant_id) VALUES (:id,:tenant)"),
                {"id": customer, "tenant": tenant},
            )
            repo = ConversationRepository()
            service = ConversationService(repo)
            conversation = repo.create(db, principal)["id"]
            other_conversation = repo.create(db, principal)["id"]
            repo.append_message(db, tenant, conversation, "customer", "earlier", uuid4())
            repo.append_message(db, tenant, conversation, "internal_note", "private note", uuid4())
            repo.append_message(db, tenant, other_conversation, "assistant", "other chat", uuid4())
            message, _ = service.send_customer(db, principal, conversation, uuid4(), "question")

            @contextmanager
            def sessions():
                yield db

            class Search:
                def search(self, tenant_id, widget_id, query, audience, limit=5):
                    assert (tenant_id, widget_id, audience) == (tenant, widget, "customer")
                    return []

            context = CustomerContext(sessions, Search())
            question, history, snippets = context.prepare(principal, conversation, message.id)
            assert question == "question" and history == [{"kind": "customer", "text": "earlier"}]
            assert snippets == []
            for invalid in (
                principal.model_copy(update={"tenant_id": other}),
                principal.model_copy(update={"customer_id": uuid4()}),
                principal.model_copy(update={"conversation_scope": other_conversation}),
            ):
                with pytest.raises(ContextUnavailable):
                    context.prepare(invalid, conversation, message.id)

            db.execute(
                text(
                    "CREATE TEMP TABLE widget_sections (tenant_id uuid,widget_id uuid,section_id uuid) ON COMMIT DROP"
                )
            )
            section, foreign_section = uuid4(), uuid4()
            for tid, sid in ((tenant, section), (other, foreign_section)):
                db.execute(
                    text(
                        "INSERT INTO article_sections (id,tenant_id,name) VALUES (:id,:tenant,:name)"
                    ),
                    {"id": sid, "tenant": tid, "name": str(sid)},
                )
            db.execute(
                text("INSERT INTO widget_sections VALUES (:tenant,:widget,:section)"),
                {"tenant": tenant, "widget": widget, "section": section},
            )
            articles = KnowledgeRepository()
            for tid, sid, visibility, title in (
                (tenant, section, "public", "allowed"),
                (tenant, section, "internal", "internal secret"),
                (other, foreign_section, "public", "foreign secret"),
            ):
                articles.create_article(
                    db,
                    tid,
                    uuid4(),
                    ArticleInput(
                        section_id=sid,
                        title=title,
                        body="question authorized information",
                        visibility=visibility,
                        status="published",
                    ),
                )
            ids = (
                db.execute(
                    text("SELECT id FROM articles WHERE tenant_id IN (:tenant,:other) ORDER BY id"),
                    {"tenant": tenant, "other": other},
                )
                .scalars()
                .all()
            )

            class PoisonedSearch:
                def search(self, *args, **kwargs):
                    return [
                        KnowledgeSnippet(
                            article_id=aid,
                            revision=1,
                            title="forged",
                            text="poisoned secret",
                            visibility="public",
                        )
                        for aid in ids
                    ]

            _, _, safe_sources = CustomerContext(sessions, PoisonedSearch()).prepare(
                principal, conversation, message.id
            )
            assert [source.title for source in safe_sources] == ["allowed"]
            assert "poisoned secret" not in safe_sources[0].text
        finally:
            transaction.rollback()
            engine.dispose()
