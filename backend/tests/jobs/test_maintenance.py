from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text

from ips.core.config import Settings
from ips.platform.retention import purge_expired
from ips.tickets.auto_close import close_due_tickets


@pytest.fixture
def db():
    engine = create_engine(Settings().database_url)
    with engine.connect() as conn, conn.begin() as transaction:
        try:
            yield conn
        finally:
            transaction.rollback()
    engine.dispose()


def organization(db):
    tenant = uuid4()
    db.execute(text("INSERT INTO organizations (id, name) VALUES (:id,'test')"), {"id": tenant})
    return tenant


def conversation(db, tenant, when):
    scope = {"tenant": tenant, "id": uuid4(), "widget": uuid4(), "customer": uuid4(), "when": when}
    db.execute(
        text("""INSERT INTO widgets
        (id,tenant_id,name,public_key,color,greeting,allowed_origins)
        VALUES (:widget,:tenant,'support',:key,'#224488','Welcome',ARRAY['http://localhost'])"""),
        {**scope, "key": str(scope["widget"])},
    )
    db.execute(text("INSERT INTO customers (id,tenant_id) VALUES (:customer,:tenant)"), scope)
    db.execute(
        text("""INSERT INTO conversations (id,tenant_id,widget_id,customer_id,updated_at)
        VALUES (:id,:tenant,:widget,:customer,:when)"""),
        scope,
    )
    return scope


def ticket(db, scope, when, status):
    identifier = uuid4()
    db.execute(
        text("""INSERT INTO tickets (id, tenant_id, conversation_id, status, priority, resolved_at)
            VALUES (:ticket,:tenant,:id,:status,'normal',:when)"""),
        {**scope, "ticket": identifier, "status": status, "when": when},
    )
    return identifier


def test_auto_close_only_due_resolved_tickets_without_faking_confirmation(db):
    now = datetime(2026, 10, 25, 1, 30, tzinfo=UTC)
    alpha, beta = organization(db), organization(db)
    resolved_at = now - timedelta(hours=72)
    a, b, c, d = [conversation(db, tenant, resolved_at) for tenant in (alpha, alpha, alpha, beta)]
    due = ticket(db, a, resolved_at, "resolved")
    fresh = ticket(db, b, resolved_at + timedelta(seconds=1), "resolved")
    active = ticket(db, c, None, "waiting_customer")
    other = ticket(db, d, resolved_at, "resolved")
    assert close_due_tickets(db, alpha, now, 72) == 1
    assert close_due_tickets(db, alpha, now, 72) == 0
    rows = {
        row.id: (row.status, row.closed_at, row.version)
        for row in db.execute(
            text("SELECT id,status,closed_at,version FROM tickets WHERE id=ANY(:ids)"),
            {"ids": [due, fresh, active, other]},
        )
    }
    assert rows == {
        due: ("closed", now, 2),
        fresh: ("resolved", None, 1),
        active: ("waiting_customer", None, 1),
        other: ("resolved", None, 1),
    }
    assert (
        db.scalar(text("SELECT outcome FROM conversations WHERE id=:id"), {"id": a["id"]})
        == "unconfirmed"
    )


def add_attachment_and_source(db, scope, when):
    identifier = uuid4()
    message_id = uuid4()
    params = {**scope, "attachment": identifier, "message": message_id, "time": when}
    db.execute(
        text("""INSERT INTO messages (id,tenant_id,conversation_id,seq,kind,text,sources,created_at)
        VALUES (:message,:tenant,:id,1,'assistant','Answer',CAST(:sources AS jsonb),:time)"""),
        {**params, "sources": '[{"revision":1}]'},
    )
    db.execute(
        text("""INSERT INTO attachments (id,tenant_id,conversation_id,created_at)
        VALUES (:attachment,:tenant,:id,:time)"""),
        params,
    )
    db.execute(
        text("""INSERT INTO message_attachments (tenant_id,conversation_id,message_id,attachment_id)
        VALUES (:tenant,:id,:message,:attachment)"""),
        params,
    )
    return identifier, message_id


def test_retention_cleans_old_attachment_bytes_and_sources_for_one_idle_tenant(db):
    # These are the columns and restrictive attachment FK of the upcoming C09 schema.
    db.execute(
        text("""CREATE TEMP TABLE attachments (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, conversation_id uuid NOT NULL,
        created_at timestamptz NOT NULL, content bytea NOT NULL DEFAULT decode('ab','hex'),
        UNIQUE (tenant_id,conversation_id,id)
    ) ON COMMIT DROP""")
    )
    db.execute(
        text("""CREATE TEMP TABLE message_attachments (
        tenant_id uuid NOT NULL, conversation_id uuid NOT NULL, message_id uuid NOT NULL,
        attachment_id uuid NOT NULL, FOREIGN KEY (tenant_id,conversation_id,attachment_id)
        REFERENCES attachments(tenant_id,conversation_id,id)
    ) ON COMMIT DROP""")
    )
    now = datetime(2026, 10, 25, tzinfo=UTC)
    old = now - timedelta(days=91)
    alpha, beta = organization(db), organization(db)
    stale = conversation(db, alpha, old)
    reopened = conversation(db, alpha, now)
    active = conversation(db, alpha, old)
    other = conversation(db, beta, old)
    ticket(db, active, None, "in_progress")
    items = [
        add_attachment_and_source(db, scope, old) for scope in (stale, reopened, active, other)
    ]
    purge_expired(db, alpha, now, 90)
    purge_expired(db, alpha, now, 90)
    remaining = set(db.execute(text("SELECT id FROM attachments")).scalars())
    assert remaining == {attachment for attachment, _ in items[1:]}
    assert db.scalar(text("SELECT count(*) FROM message_attachments")) == 3
    for index, (_, message) in enumerate(items):
        row = db.execute(
            text("SELECT text,sources FROM messages WHERE id=:id"), {"id": message}
        ).one()
        assert row.text == "Answer"
        assert row.sources == ([] if index == 0 else [{"revision": 1}])


def test_retention_removes_only_old_import_links_for_the_selected_tenant(db):
    db.execute(
        text("""CREATE TEMP TABLE knowledge_import_articles (
        tenant_id uuid NOT NULL, import_id uuid NOT NULL, position integer NOT NULL
    ) ON COMMIT DROP""")
    )
    now = datetime(2026, 10, 25, tzinfo=UTC)
    old = now - timedelta(days=91)
    alpha, beta = organization(db), organization(db)
    imported = []
    for tenant, when in ((alpha, old), (alpha, now), (beta, old)):
        identifier, section = uuid4(), uuid4()
        params = {"id": identifier, "section": section, "tenant": tenant, "created": when}
        db.execute(
            text("INSERT INTO article_sections (id,tenant_id,name) VALUES (:section,:tenant,:id)"),
            {**params, "id": str(identifier)},
        )
        db.execute(
            text("""INSERT INTO knowledge_imports (id,tenant_id,section_id,state,created_at)
                VALUES (:id,:tenant,:section,'ready',:created)"""),
            params,
        )
        db.execute(
            text("""INSERT INTO knowledge_import_articles (tenant_id,import_id,position)
                VALUES (:tenant,:id,0)"""),
            params,
        )
        imported.append(identifier)
    purge_expired(db, alpha, now, 90)
    assert set(
        db.execute(text("SELECT import_id FROM knowledge_import_articles")).scalars()
    ) == set(imported[1:])
    assert (
        db.scalar(
            text("SELECT count(*) FROM knowledge_imports WHERE id=ANY(:ids)"), {"ids": imported}
        )
        == 3
    )
