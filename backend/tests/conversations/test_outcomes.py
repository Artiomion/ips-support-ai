from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from ips.conversations.repository import ConversationRepository
from ips.core.config import Settings
from ips.core.ports import CustomerPrincipal, TenantContext
from ips.main import create_app
from ips.platform.auth import require_csrf, tenant_context
from ips.platform.widget_api import customer_principal


@pytest.fixture
def context():
    app = create_app(Settings())
    tenant, widget, customer = uuid4(), uuid4(), uuid4()
    principal = CustomerPrincipal(tenant_id=tenant, widget_id=widget, customer_id=customer)
    staff = TenantContext(
        tenant_id=tenant, user_id=uuid4(), membership_id=uuid4(), role="specialist"
    )
    app.dependency_overrides[customer_principal] = lambda: principal
    app.dependency_overrides[tenant_context] = lambda: staff
    app.dependency_overrides[require_csrf] = lambda: None
    with Session(app.state.engine) as db, db.begin():
        db.execute(text("INSERT INTO organizations (id,name) VALUES (:id,'C07')"), {"id": tenant})
        db.execute(
            text("""INSERT INTO widgets (id,tenant_id,name,public_key,color,greeting,allowed_origins)
            VALUES (:id,:tenant,'test',:key,'#ffffff','hello',ARRAY['https://example.test'])"""),
            {"id": widget, "tenant": tenant, "key": str(widget)},
        )
        db.execute(
            text("INSERT INTO customers (id,tenant_id) VALUES (:id,:tenant)"),
            {"id": customer, "tenant": tenant},
        )
        conversation = ConversationRepository().create(db, principal)["id"]
    with TestClient(app) as client:
        yield app, client, principal, staff, conversation
    with Session(app.state.engine) as db, db.begin():
        for table in (
            "jobs",
            "tickets",
            "ai_runs",
            "messages",
            "conversations",
            "customers",
            "widgets",
        ):
            db.execute(text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), {"tenant": tenant})
        db.execute(text("DELETE FROM organizations WHERE id=:tenant"), {"tenant": tenant})
    app.state.engine.dispose()


def completed_ai_answer(db, principal, conversation, question_id=None):
    repo = ConversationRepository()
    if question_id is None:
        question_id = repo.append_message(
            db, principal.tenant_id, conversation, "customer", "Question", uuid4()
        )["id"]
    version = db.execute(
        text("SELECT version FROM conversations WHERE tenant_id=:tenant AND id=:id"),
        {"tenant": principal.tenant_id, "id": conversation},
    ).scalar_one()
    run_id = uuid4()
    db.execute(
        text("""INSERT INTO ai_runs (id,tenant_id,conversation_id,input_message_id,state,generation_version)
        VALUES (:run,:tenant,:conversation,:question,'completed',:version)"""),
        {
            "run": run_id,
            "tenant": principal.tenant_id,
            "conversation": conversation,
            "question": question_id,
            "version": version,
        },
    )
    repo.append_message(db, principal.tenant_id, conversation, "assistant", "Answer", run_id)


def test_explicit_ai_confirmation_and_customer_isolation(context):
    app, client, principal, _, conversation = context
    path = f"/api/v1/public/conversations/{conversation}/confirm-ai"
    assert client.post(path).status_code == 409
    with Session(app.state.engine) as db, db.begin():
        outcome = db.execute(
            text("SELECT outcome FROM conversations WHERE id=:id"), {"id": conversation}
        ).scalar_one()
        assert outcome == "unconfirmed"
        completed_ai_answer(db, principal, conversation)
    result = client.post(path)
    assert result.status_code == 200 and result.json()["outcome"] == "ai_confirmed"
    app.dependency_overrides[customer_principal] = lambda: principal.model_copy(
        update={"customer_id": uuid4()}
    )
    assert client.post(path).status_code == 404


def test_old_ai_answer_cannot_replace_human_confirmation_or_answer_new_question(context):
    app, client, principal, _, conversation = context
    path = f"/api/v1/public/conversations/{conversation}"
    repo = ConversationRepository()
    with Session(app.state.engine) as db, db.begin():
        completed_ai_answer(db, principal, conversation)
        app.state.ports.tickets.escalate(db, principal, conversation, "help", None, "normal")
        app.state.ports.conversations.confirm_human(db, principal.tenant_id, conversation)
        db.execute(
            text("UPDATE tickets SET status='resolved' WHERE tenant_id=:tenant"),
            {"tenant": principal.tenant_id},
        )
    assert client.post(path + "/resume-bot").status_code == 200
    assert client.post(path + "/confirm-ai").status_code == 409
    with Session(app.state.engine) as db, db.begin():
        assert (
            db.execute(
                text("SELECT outcome FROM conversations WHERE id=:id"), {"id": conversation}
            ).scalar_one()
            == "human_confirmed"
        )
        repo.append_message(
            db, principal.tenant_id, conversation, "customer", "Another question", uuid4()
        )
    assert client.post(path + "/confirm-ai").status_code == 409
    with Session(app.state.engine) as db:
        assert (
            db.execute(
                text("SELECT outcome FROM conversations WHERE id=:id"), {"id": conversation}
            ).scalar_one()
            == "human_confirmed"
        )


def test_only_answer_to_latest_customer_question_can_be_confirmed(context):
    app, client, principal, _, conversation = context
    repo = ConversationRepository()
    path = f"/api/v1/public/conversations/{conversation}/confirm-ai"
    with Session(app.state.engine) as db, db.begin():
        first = repo.append_message(
            db, principal.tenant_id, conversation, "customer", "First", uuid4()
        )["id"]
        second = repo.append_message(
            db, principal.tenant_id, conversation, "customer", "Second", uuid4()
        )["id"]
        completed_ai_answer(db, principal, conversation, first)
    assert client.post(path).status_code == 409
    with Session(app.state.engine) as db, db.begin():
        completed_ai_answer(db, principal, conversation, second)
    assert client.post(path).status_code == 200


def test_confirm_real_worker_answer_with_postgres_budget(context):
    from sqlalchemy.orm import sessionmaker

    from ips.ai.fake import FakeLLMProvider
    from ips.ai.orchestrator import Orchestrator
    from ips.knowledge.repository import KnowledgeRepository
    from ips.knowledge.retrieval import PostgresKnowledgeSearch
    from ips.knowledge.schemas import ArticleInput
    from ips.tickets.locks import lock_handoff_memberships

    app, client, principal, staff, conversation = context
    section = uuid4()
    with Session(app.state.engine) as db, db.begin():
        db.execute(
            text(
                "INSERT INTO article_sections (id,tenant_id,name) VALUES (:id,:tenant,'C07 test')"
            ),
            {"id": section, "tenant": principal.tenant_id},
        )
        db.execute(
            text(
                "INSERT INTO widget_sections (tenant_id,widget_id,section_id) VALUES (:tenant,:widget,:section)"
            ),
            {"tenant": principal.tenant_id, "widget": principal.widget_id, "section": section},
        )
        KnowledgeRepository().create_article(
            db,
            principal.tenant_id,
            staff.user_id,
            ArticleInput(
                section_id=section,
                title="Refund",
                body="refund policy",
                visibility="public",
                status="published",
            ),
        )
    try:
        response = client.post(
            f"/api/v1/public/conversations/{conversation}/messages",
            json={"client_message_id": str(uuid4()), "text": "refund"},
        )
        assert response.status_code == 201
        sessions = sessionmaker(app.state.engine)
        worker = Orchestrator(
            sessions,
            app.state.usage_budget,
            PostgresKnowledgeSearch(sessions),
            FakeLLMProvider(),
            tickets=app.state.ports.tickets,
            handoff_guard=lock_handoff_memberships,
            tenant_id=principal.tenant_id,
        )
        assert worker.run_once()
        result = client.post(f"/api/v1/public/conversations/{conversation}/confirm-ai")
        assert result.status_code == 200 and result.json()["outcome"] == "ai_confirmed"
        with Session(app.state.engine) as db:
            assert (
                db.execute(
                    text("SELECT state FROM usage_reservations WHERE tenant_id=:tenant"),
                    {"tenant": principal.tenant_id},
                ).scalar_one()
                == "succeeded"
            )
    finally:
        with Session(app.state.engine) as db, db.begin():
            params = {"tenant": principal.tenant_id}
            db.execute(text("DELETE FROM usage_reservations WHERE tenant_id=:tenant"), params)
            db.execute(text("DELETE FROM widget_sections WHERE tenant_id=:tenant"), params)
            db.execute(
                text("UPDATE articles SET current_revision_id=NULL WHERE tenant_id=:tenant"), params
            )
            for table in ("article_revisions", "articles", "article_sections"):
                db.execute(text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), params)


@pytest.mark.parametrize("action", ["confirm-ai", "resume-bot"])
def test_widget_cors_for_outcome_actions(context, action):
    app, client, _, _, conversation = context
    path = f"/api/v1/public/conversations/{conversation}/{action}"
    allowed = app.state.settings.widget_origin
    foreign = "https://foreign.example"
    for origin, permitted in ((allowed, True), (foreign, False)):
        headers = {
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        }
        preflight = client.options(path, headers=headers)
        assert preflight.status_code == (200 if permitted else 403)
        assert preflight.headers.get("access-control-allow-origin") == (
            origin if permitted else None
        )
        response = client.post(path, headers={"Origin": origin})
        assert response.headers.get("access-control-allow-origin") == (
            origin if permitted else None
        )
    invalid_method = client.options(
        path,
        headers={
            "Origin": allowed,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert invalid_method.status_code == 403


def test_staff_reply_queue_handler_receives_verified_tenant(context):
    from ips.core.jobs import drain_once

    app, client, principal, _, conversation = context
    foreign_tenant, foreign_widget, foreign_customer = uuid4(), uuid4(), uuid4()
    foreign_principal = CustomerPrincipal(
        tenant_id=foreign_tenant, widget_id=foreign_widget, customer_id=foreign_customer
    )
    with Session(app.state.engine) as db, db.begin():
        db.execute(
            text("INSERT INTO organizations (id,name) VALUES (:id,'other')"), {"id": foreign_tenant}
        )
        db.execute(
            text(
                "INSERT INTO widgets (id,tenant_id,name,public_key,color,greeting,allowed_origins) VALUES (:id,:tenant,'other',:key,'#ffffff','hello',ARRAY[]::text[])"
            ),
            {"id": foreign_widget, "tenant": foreign_tenant, "key": str(foreign_widget)},
        )
        db.execute(
            text("INSERT INTO customers (id,tenant_id) VALUES (:id,:tenant)"),
            {"id": foreign_customer, "tenant": foreign_tenant},
        )
        foreign_conversation = ConversationRepository().create(db, foreign_principal)["id"]
    response = client.post(
        f"/api/v1/conversations/{conversation}/messages",
        json={"client_message_id": str(uuid4()), "text": "reply", "internal": False},
    )
    assert response.status_code == 201
    received = []

    def handle(payload):
        received.append(payload)
        assert payload["tenant_id"] == str(principal.tenant_id)
        with Session(app.state.engine) as db:
            own = db.execute(
                text("SELECT id FROM conversations WHERE tenant_id=:tenant AND id=:id"),
                {"tenant": payload["tenant_id"], "id": payload["conversation_id"]},
            ).scalar_one()
            other = db.execute(
                text("SELECT id FROM conversations WHERE tenant_id=:tenant AND id=:id"),
                {"tenant": payload["tenant_id"], "id": foreign_conversation},
            ).scalar_one_or_none()
            assert own == conversation and other is None

    try:
        assert drain_once(app.state.engine, {"staff_reply": handle})
        assert len(received) == 1 and received[0]["message_id"] == response.json()["id"]
    finally:
        with Session(app.state.engine) as db, db.begin():
            for table in ("conversations", "customers", "widgets"):
                db.execute(
                    text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), {"tenant": foreign_tenant}
                )
            db.execute(
                text("DELETE FROM organizations WHERE id=:tenant"), {"tenant": foreign_tenant}
            )


def test_human_mode_cannot_confirm_ai_and_active_ticket_blocks_resume(context):
    app, client, principal, staff, conversation = context
    with Session(app.state.engine) as db, db.begin():
        app.state.ports.tickets.escalate(db, principal, conversation, "help", None, "normal")
    prefix = f"/api/v1/public/conversations/{conversation}"
    assert client.post(prefix + "/confirm-ai").status_code == 409
    assert client.post(prefix + "/resume-bot").status_code == 409
    with Session(app.state.engine) as db, db.begin():
        row = db.execute(
            text("SELECT mode,outcome FROM conversations WHERE id=:id"), {"id": conversation}
        ).one()
        assert (row.mode, row.outcome) == ("human", "unconfirmed")
        app.state.ports.conversations.confirm_human(db, principal.tenant_id, conversation)
        db.execute(
            text("UPDATE tickets SET status='resolved' WHERE tenant_id=:tenant"),
            {"tenant": principal.tenant_id},
        )
    result = client.post(prefix + "/resume-bot")
    assert result.status_code == 200 and result.json()["mode"] == "bot"
    assert result.json()["outcome"] == "human_confirmed"
    with Session(app.state.engine) as db:
        stats = app.state.ports.conversations.metrics(db, staff, principal.widget_id)
        assert (stats.conversations, stats.ai_confirmed, stats.handoffs) == (1, 0, 1)


def test_staff_note_has_no_event_and_duplicate_public_reply_is_idempotent(context):
    app, client, principal, _, conversation = context
    with Session(app.state.engine) as db, db.begin():
        app.state.ports.tickets.escalate(db, principal, conversation, "help", None, "normal")
    path = f"/api/v1/conversations/{conversation}/messages"
    note = {"client_message_id": str(uuid4()), "text": "private", "internal": True}
    reply = {"client_message_id": str(uuid4()), "text": "public", "internal": False}
    assert client.post(path, json=note).status_code == 201
    with Session(app.state.engine) as db:
        assert (
            db.execute(
                text("SELECT count(*) FROM jobs WHERE tenant_id=:tenant"),
                {"tenant": principal.tenant_id},
            ).scalar_one()
            == 0
        )
    assert client.post(path, json=reply).status_code == 201
    assert client.post(path, json=reply).status_code == 200
    with Session(app.state.engine) as db:
        assert (
            db.execute(
                text("SELECT count(*) FROM jobs WHERE tenant_id=:tenant"),
                {"tenant": principal.tenant_id},
            ).scalar_one()
            == 1
        )
        assert (
            db.execute(
                text("SELECT first_staff_reply_at FROM tickets WHERE tenant_id=:tenant"),
                {"tenant": principal.tenant_id},
            ).scalar_one()
            is not None
        )


def test_enqueue_failure_rolls_back_reply_and_first_response(context):
    app, client, principal, _, conversation = context
    with Session(app.state.engine) as db, db.begin():
        app.state.ports.tickets.escalate(db, principal, conversation, "help", None, "normal")

    class FailingNotification:
        def enqueue_staff_reply(self, uow, event):
            raise RuntimeError("failed")

    app.state.ports.notifications = FailingNotification()
    with pytest.raises(RuntimeError):
        client.post(
            f"/api/v1/conversations/{conversation}/messages",
            json={"client_message_id": str(uuid4()), "text": "public", "internal": False},
        )
    with Session(app.state.engine) as db:
        assert (
            db.execute(
                text("SELECT count(*) FROM messages WHERE tenant_id=:tenant AND kind='specialist'"),
                {"tenant": principal.tenant_id},
            ).scalar_one()
            == 0
        )
        assert (
            db.execute(
                text("SELECT first_staff_reply_at FROM tickets WHERE tenant_id=:tenant"),
                {"tenant": principal.tenant_id},
            ).scalar_one()
            is None
        )


def test_demo_seed_is_explicit_idempotent_and_tenant_scoped(context):
    from ips.knowledge.seed import seed_demo_knowledge

    app, _, principal, staff, _ = context
    with app.state.engine.connect() as connection:
        transaction = connection.begin()
        try:
            with Session(bind=connection) as db:
                first = seed_demo_knowledge(db, principal.tenant_id, staff.user_id)
                second = seed_demo_knowledge(db, principal.tenant_id, staff.user_id)
                assert first == second and len(first) == 2
                rows = (
                    db.execute(
                        text("SELECT visibility FROM articles WHERE tenant_id=:tenant"),
                        {"tenant": principal.tenant_id},
                    )
                    .scalars()
                    .all()
                )
                assert sorted(rows) == ["internal", "public"]
        finally:
            transaction.rollback()


def test_pending_staff_reply_does_not_block_invitation_worker(context):
    from ips.core.jobs import drain_once, enqueue

    app, client, principal, _, conversation = context
    path = f"/api/v1/conversations/{conversation}/messages"
    reply = {"client_message_id": str(uuid4()), "text": "public", "internal": False}
    assert client.post(path, json=reply).status_code == 201
    with app.state.engine.begin() as connection:
        enqueue(
            connection,
            principal.tenant_id,
            "invitation",
            {"test": "C07"},
            f"c07-invitation:{uuid4()}",
        )
        connection.execute(
            text("UPDATE jobs SET run_after=now()-interval '1 year' WHERE tenant_id=:tenant"),
            {"tenant": principal.tenant_id},
        )
    seen = []
    assert drain_once(app.state.engine, {"invitation": seen.append})
    assert seen == [{"test": "C07"}]
    with Session(app.state.engine) as db:
        assert (
            db.execute(
                text("SELECT status FROM jobs WHERE tenant_id=:tenant AND kind='staff_reply'"),
                {"tenant": principal.tenant_id},
            ).scalar_one()
            == "pending"
        )


def test_resume_bot_and_handoff_cannot_leave_active_ticket_in_bot_mode(context):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from fastapi import HTTPException

    app, _, principal, _, conversation = context
    start = Barrier(2, timeout=5)

    def run(action):
        with Session(app.state.engine) as db:
            start.wait()
            try:
                if action == "resume":
                    app.state.ports.conversations.resume_bot(db, principal, conversation)
                else:
                    app.state.ports.tickets.escalate(
                        db, principal, conversation, "help", None, "normal"
                    )
                db.commit()
                return 200
            except HTTPException as error:
                db.rollback()
                return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(run, ("resume", "handoff")))
    assert outcomes[0] in (200, 409) and outcomes[1] == 200
    with Session(app.state.engine) as db:
        assert (
            db.execute(
                text("SELECT mode FROM conversations WHERE id=:id"), {"id": conversation}
            ).scalar_one()
            == "human"
        )
        assert app.state.ports.tickets.has_active(db, principal.tenant_id, conversation)
