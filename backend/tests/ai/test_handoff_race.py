from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from ips.ai.orchestrator import Orchestrator
from ips.ai.provider import ProviderFailure
from ips.conversations.repository import ConversationRepository
from ips.conversations.service import ConversationService
from ips.core.config import Settings
from ips.core.ports import AnswerResult, Citation, CustomerPrincipal, KnowledgeSnippet, Reservation
from ips.knowledge.repository import KnowledgeRepository
from ips.knowledge.retrieval import PostgresKnowledgeSearch
from ips.knowledge.schemas import ArticleInput
from ips.tickets.service import TicketService


class Budget:
    def __init__(self, reject=False):
        self.reject = reject
        self.settlements = []

    def reserve(self, tenant_id, run_id):
        if self.reject:
            raise RuntimeError("quota")
        return Reservation(id=uuid4(), tenant_id=tenant_id, run_id=run_id)

    def settle(self, reservation, usage, failed):
        self.settlements.append(failed)


class Provider:
    def __init__(self, pause=False, malformed=False):
        self.started, self.resume = Event(), Event()
        self.pause, self.malformed = pause, malformed

    def answer(self, data, timeout_seconds):
        self.started.set()
        if self.pause:
            assert self.resume.wait(10)
        if self.malformed:
            return {"text": "bad", "needs_human": True, "priority": "urgent"}
        return AnswerResult(
            text="answer",
            citations=[
                Citation.model_validate(
                    {field: data.snippets[0][field] for field in Citation.model_fields}
                )
            ],
            needs_human=False,
            category=None,
            priority="normal",
            reason=None,
        )


@pytest.fixture
def scenario():
    engine = create_engine(Settings().database_url)
    factory = sessionmaker(engine)
    tenant, widget, customer = uuid4(), uuid4(), uuid4()
    principal = CustomerPrincipal(tenant_id=tenant, widget_id=widget, customer_id=customer)
    service = ConversationService(ConversationRepository())
    tickets = TicketService(service)
    with factory.begin() as db:
        db.execute(text("INSERT INTO organizations (id,name) VALUES (:id,'test')"), {"id": tenant})
        db.execute(
            text("""INSERT INTO widgets
          (id,tenant_id,name,public_key,color,greeting,allowed_origins)
          VALUES (:id,:tenant,'test',:key,'#ffffff','hello',ARRAY['https://example.test'])"""),
            {"id": widget, "tenant": tenant, "key": str(uuid4())},
        )
        db.execute(
            text("INSERT INTO customers (id,tenant_id) VALUES (:id,:tenant)"),
            {"id": customer, "tenant": tenant},
        )
        conversation = service.repository.create(db, principal)["id"]
        service.send_customer(db, principal, conversation, uuid4(), "hello")
    yield factory, principal, conversation, tickets
    with factory.begin() as db:
        if db.execute(text("SELECT to_regclass('usage_reservations')")).scalar_one():
            db.execute(
                text("DELETE FROM usage_reservations WHERE tenant_id=:tenant"), {"tenant": tenant}
            )
        for table in ("tickets", "ai_runs", "messages", "conversations", "customers", "widgets"):
            db.execute(text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), {"tenant": tenant})
        db.execute(text("DELETE FROM organizations WHERE id=:tenant"), {"tenant": tenant})
    engine.dispose()


@pytest.fixture
def public_source(scenario):
    factory, principal, _, _ = scenario
    section = uuid4()
    params = {"tenant": principal.tenant_id, "widget": principal.widget_id, "section": section}
    with factory.begin() as db:
        db.execute(
            text(
                "INSERT INTO article_sections (id,tenant_id,name) VALUES (:section,:tenant,'Test')"
            ),
            params,
        )
        db.execute(
            text("""INSERT INTO widget_sections (tenant_id,widget_id,section_id)
            VALUES (:tenant,:widget,:section)"""),
            params,
        )
        article = KnowledgeRepository().create_article(
            db,
            principal.tenant_id,
            uuid4(),
            ArticleInput(
                section_id=section,
                title="Public source",
                body="hello follow-up. different conversation.",
                visibility="public",
                status="published",
            ),
        )
    yield article
    with factory.begin() as db:
        db.execute(text("DELETE FROM widget_sections WHERE tenant_id=:tenant"), params)
        db.execute(
            text("UPDATE articles SET current_revision_id=NULL WHERE tenant_id=:tenant"), params
        )
        for table in ("article_revisions", "articles", "article_sections"):
            db.execute(text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), params)


@pytest.fixture
def search(scenario, public_source):
    factory, _, _, _ = scenario
    return PostgresKnowledgeSearch(factory)


def assistants(factory, principal):
    with factory() as db:
        return db.execute(
            text("SELECT count(*) FROM messages WHERE tenant_id=:tenant AND kind='assistant'"),
            {"tenant": principal.tenant_id},
        ).scalar_one()


def test_handoff_while_provider_is_running_discards_reply(scenario, search):
    factory, principal, conversation, tickets = scenario
    provider, budget = Provider(pause=True), Budget()
    worker = Orchestrator(factory, budget, search, provider, tenant_id=principal.tenant_id)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker.run_once)
        assert provider.started.wait(10)
        with factory.begin() as db:
            tickets.escalate(db, principal, conversation, "help", None, "normal")
        provider.resume.set()
        assert future.result(timeout=10)
    assert assistants(factory, principal) == 0
    assert budget.settlements == [False]


def test_duplicate_job_does_not_duplicate_answer(scenario, search):
    factory, principal, _, _ = scenario
    worker = Orchestrator(factory, Budget(), search, Provider(), tenant_id=principal.tenant_id)
    assert worker.run_once()
    assert not worker.run_once()
    assert assistants(factory, principal) == 1


@pytest.fixture
def queued_runs(scenario):
    factory, principal, conversation, _ = scenario
    second_id, first_id = sorted((uuid4(), uuid4()))
    service = ConversationService(ConversationRepository())
    with factory.begin() as db:
        service.send_customer(db, principal, conversation, uuid4(), "follow-up")
        db.execute(
            text("""UPDATE ai_runs AS r
            SET id=CASE WHEN m.seq=1 THEN CAST(:first AS uuid) ELSE CAST(:second AS uuid) END
            FROM messages AS m WHERE r.tenant_id=:tenant AND r.conversation_id=:conversation
              AND m.tenant_id=r.tenant_id AND m.conversation_id=r.conversation_id
              AND m.id=r.input_message_id"""),
            {
                "tenant": principal.tenant_id,
                "conversation": conversation,
                "first": first_id,
                "second": second_id,
            },
        )
    return first_id, second_id


@pytest.mark.parametrize("expired", [False, True], ids=["pending", "expired"])
def test_answers_follow_message_sequence(scenario, queued_runs, expired, search):
    factory, principal, conversation, _ = scenario
    first_id, second_id = queued_runs
    if expired:
        with factory.begin() as db:
            db.execute(
                text("""UPDATE ai_runs SET state='running', lease_until=now()-interval '1 second'
                WHERE id=:id"""),
                {"id": first_id},
            )
    worker = Orchestrator(factory, Budget(), search, Provider(), tenant_id=principal.tenant_id)

    assert worker.run_once()
    assert worker.run_once()

    with factory() as db:
        answered = (
            db.execute(
                text("""SELECT client_message_id FROM messages
            WHERE tenant_id=:tenant AND conversation_id=:conversation AND kind='assistant'
            ORDER BY seq"""),
                {"tenant": principal.tenant_id, "conversation": conversation},
            )
            .scalars()
            .all()
        )
    assert answered == [first_id, second_id]


def test_locked_earlier_run_only_blocks_its_conversation(scenario, queued_runs, search):
    factory, principal, _, _ = scenario
    first_id, _ = queued_runs
    service = ConversationService(ConversationRepository())
    with factory.begin() as db:
        other = service.repository.create(db, principal)["id"]
        service.send_customer(db, principal, other, uuid4(), "different conversation")
    worker = Orchestrator(factory, Budget(), search, Provider(), tenant_id=principal.tenant_id)

    with factory.begin() as db:
        db.execute(text("SELECT id FROM ai_runs WHERE id=:id FOR UPDATE"), {"id": first_id})
        assert worker.run_once()
        assert not worker.run_once()

    with factory() as db:
        replied_to = (
            db.execute(
                text(
                    "SELECT conversation_id FROM messages WHERE tenant_id=:tenant AND kind='assistant'"
                ),
                {"tenant": principal.tenant_id},
            )
            .scalars()
            .all()
        )
    assert replied_to == [other]
    assert worker.run_once()
    assert worker.run_once()


def test_exhausted_budget_keeps_manual_handoff_available(scenario, search):
    factory, principal, conversation, tickets = scenario
    provider = Provider()
    assert Orchestrator(
        factory, Budget(reject=True), search, provider, tenant_id=principal.tenant_id
    ).run_once()
    assert not provider.started.is_set()
    with factory.begin() as db:
        tickets.escalate(db, principal, conversation, "help", None, "normal")
    assert assistants(factory, principal) == 0


def test_malformed_provider_result_does_not_create_ticket(scenario, search):
    factory, principal, _, _ = scenario
    assert Orchestrator(
        factory, Budget(), search, Provider(malformed=True), tenant_id=principal.tenant_id
    ).run_once()
    with factory() as db:
        count = db.execute(
            text("SELECT count(*) FROM tickets WHERE tenant_id=:tenant"),
            {"tenant": principal.tenant_id},
        ).scalar_one()
    assert count == 0
    assert assistants(factory, principal) == 0


@pytest.mark.parametrize("malformed", [False, True], ids=["old_succeeds", "old_fails"])
def test_expired_lease_is_recovered_and_only_current_attempt_settles(scenario, malformed, search):
    factory, principal, _, _ = scenario
    old, current = Provider(pause=True, malformed=malformed), Provider(pause=True)
    budget = Budget()
    first = Orchestrator(factory, budget, search, old, tenant_id=principal.tenant_id)
    second = Orchestrator(factory, budget, search, current, tenant_id=principal.tenant_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            first_attempt = pool.submit(first.run_once)
            assert old.started.wait(5)
            with factory.begin() as db:
                db.execute(
                    text(
                        "UPDATE ai_runs SET lease_until=now()-interval '1 second' WHERE tenant_id=:tenant"
                    ),
                    {"tenant": principal.tenant_id},
                )
            second_attempt = pool.submit(second.run_once)
            assert current.started.wait(5)
            old.resume.set()
            assert first_attempt.result(timeout=5)
            current.resume.set()
            assert second_attempt.result(timeout=5)
        finally:
            old.resume.set()
            current.resume.set()
    assert assistants(factory, principal) == 1
    assert budget.settlements == [False]


def test_provider_failure_is_recorded_without_breaking_handoff(scenario, search):
    factory, principal, conversation, tickets = scenario

    class FailedProvider:
        def answer(self, data, timeout_seconds):
            raise RuntimeError("sensitive provider details")

    budget = Budget()
    assert Orchestrator(
        factory, budget, search, FailedProvider(), tenant_id=principal.tenant_id
    ).run_once()
    with factory.begin() as db:
        state, code = db.execute(
            text("SELECT state,error_code FROM ai_runs WHERE tenant_id=:tenant"),
            {"tenant": principal.tenant_id},
        ).one()
        tickets.escalate(db, principal, conversation, "help", None, "normal")
    assert (state, code) == ("failed", "generation_failed")
    assert budget.settlements == [True]


@pytest.mark.parametrize("code", ["provider_unavailable", "rate_limited"])
def test_provider_outage_shows_safe_message_and_leaves_handoff_manual(scenario, search, code):
    factory, principal, conversation, tickets = scenario

    class UnavailableProvider:
        def answer(self, data, timeout_seconds):
            raise ProviderFailure(code)

    budget = Budget()
    worker = Orchestrator(
        factory,
        budget,
        search,
        UnavailableProvider(),
        tickets=tickets,
        tenant_id=principal.tenant_id,
        demo=True,
    )
    assert worker.run_once()
    with factory.begin() as db:
        state, error = db.execute(
            text("SELECT state,error_code FROM ai_runs WHERE tenant_id=:tenant"),
            {"tenant": principal.tenant_id},
        ).one()
        answer = db.execute(
            text("""SELECT text FROM messages WHERE tenant_id=:tenant AND kind='assistant'"""),
            {"tenant": principal.tenant_id},
        ).scalar_one()
        assert answer == "Демо-режим: Не удалось подготовить ответ. Обратитесь к специалисту."
        assert (state, error) == ("failed", code)
        assert not tickets.has_active(db, principal.tenant_id, conversation)
        tickets.escalate(db, principal, conversation, "help", None, "normal")
        assert tickets.has_active(db, principal.tenant_id, conversation)
    assert budget.settlements == [True]


def test_valid_handoff_uses_ticket_port(scenario, search):
    factory, principal, conversation, tickets = scenario

    class HandoffProvider:
        def answer(self, data, timeout_seconds):
            return AnswerResult(
                text="Contact support",
                citations=[],
                needs_human=True,
                category=None,
                priority="normal",
                reason="help",
            )

    from ips.tickets.locks import lock_handoff_memberships

    worker = Orchestrator(
        factory,
        Budget(),
        search,
        HandoffProvider(),
        tickets=tickets,
        tenant_id=principal.tenant_id,
        handoff_guard=lock_handoff_memberships,
    )
    assert worker.run_once()
    with factory() as db:
        mode = db.execute(
            text("SELECT mode FROM conversations WHERE id=:id"), {"id": conversation}
        ).scalar_one()
        count = db.execute(
            text("SELECT count(*) FROM tickets WHERE tenant_id=:tenant"),
            {"tenant": principal.tenant_id},
        ).scalar_one()
    assert mode == "human"
    assert count == 1
    assert assistants(factory, principal) == 1


def test_ai_and_manual_handoff_no_lock_order_deadlock(scenario, search):
    from threading import Event

    factory, principal, conversation, tickets = scenario
    user_id, membership_id = uuid4(), uuid4()
    with factory.begin() as db:
        db.execute(
            text("INSERT INTO users (id,normalized_email,password_hash) VALUES (:id,:email,'x')"),
            {"id": user_id, "email": f"{user_id}@example.org"},
        )
        db.execute(
            text(
                "INSERT INTO memberships (id,tenant_id,user_id,role) VALUES (:id,:tenant,:user,'specialist')"
            ),
            {"id": membership_id, "tenant": principal.tenant_id, "user": user_id},
        )
    from ips.tickets.locks import lock_handoff_memberships

    membership_locked, guard_started = Event(), Event()

    def handoff_guard(db, tenant_id):
        guard_started.set()
        lock_handoff_memberships(db, tenant_id)

    errors = []

    class SignalingTicketPort:
        def escalate(self, *args):
            try:
                return tickets.escalate(*args)
            except Exception as exc:
                errors.append(exc)
                raise

    class HandoffProvider:
        def answer(self, data, timeout_seconds):
            return AnswerResult(
                text="Contact support",
                citations=[],
                needs_human=True,
                category=None,
                priority="normal",
                reason="help",
            )

    def manual_handoff():
        with factory.begin() as db:
            db.execute(
                text("SELECT id FROM memberships WHERE tenant_id=:tenant AND id=:id FOR UPDATE"),
                {"tenant": principal.tenant_id, "id": membership_id},
            )
            membership_locked.set()
            assert guard_started.wait(5)
            tickets.escalate(db, principal, conversation, "Manual", None, "normal")

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            manual = pool.submit(manual_handoff)
            assert membership_locked.wait(5)
            ai = pool.submit(
                Orchestrator(
                    factory,
                    Budget(),
                    search,
                    HandoffProvider(),
                    tickets=SignalingTicketPort(),
                    tenant_id=principal.tenant_id,
                    handoff_guard=handoff_guard,
                ).run_once
            )
            manual.result(timeout=15)
            ai.result(timeout=15)
        assert not errors, [str(error) for error in errors]
    finally:
        with factory.begin() as db:
            db.execute(
                text("UPDATE tickets SET assignee_id=NULL WHERE tenant_id=:tenant"),
                {"tenant": principal.tenant_id},
            )
            db.execute(text("DELETE FROM memberships WHERE id=:id"), {"id": membership_id})
            db.execute(text("DELETE FROM users WHERE id=:id"), {"id": user_id})


def test_handoff_without_guard_fails_closed(scenario, search):
    factory, principal, conversation, tickets = scenario

    class HandoffProvider:
        def answer(self, data, timeout_seconds):
            return AnswerResult(
                text="Contact support",
                citations=[],
                needs_human=True,
                category=None,
                priority="normal",
                reason="help",
            )

    worker = Orchestrator(
        factory,
        Budget(),
        search,
        HandoffProvider(),
        tickets=tickets,
        tenant_id=principal.tenant_id,
    )
    assert worker.run_once()
    with factory() as db:
        row = db.execute(
            text("SELECT mode FROM conversations WHERE id=:id"), {"id": conversation}
        ).one()
        run = db.execute(
            text("SELECT state, error_code FROM ai_runs WHERE conversation_id=:id"),
            {"id": conversation},
        ).one()
        assert row.mode == "bot"
        assert (run.state, run.error_code) == ("failed", "generation_failed")
        assert not db.execute(
            text("SELECT 1 FROM tickets WHERE tenant_id=:tenant"), {"tenant": principal.tenant_id}
        ).first()


def test_application_runner_processes_pending_handoff_and_stops(scenario):
    from time import monotonic, sleep

    from fastapi.testclient import TestClient

    from ips.main import create_app

    factory, principal, conversation, _ = scenario
    app = create_app(Settings(ai_runner_enabled=True))
    with TestClient(app):
        deadline = monotonic() + 8
        while monotonic() < deadline:
            with factory() as db:
                state = db.execute(
                    text("SELECT state FROM ai_runs WHERE conversation_id=:id"),
                    {"id": conversation},
                ).scalar_one()
            if state in ("completed", "failed"):
                break
            sleep(0.05)
        assert state == "completed"
        with factory() as db:
            mode = db.execute(
                text("SELECT mode FROM conversations WHERE id=:id"), {"id": conversation}
            ).scalar_one()
            count = db.execute(
                text("SELECT count(*) FROM tickets WHERE tenant_id=:tenant"),
                {"tenant": principal.tenant_id},
            ).scalar_one()
            reply = db.execute(
                text("""SELECT text FROM messages
                WHERE tenant_id=:tenant AND conversation_id=:conversation AND kind='assistant'"""),
                {"tenant": principal.tenant_id, "conversation": conversation},
            ).scalar_one()
        assert mode == "human"
        assert count == 1
        assert reply.startswith("Демо-режим: ")
        with factory() as db:
            has_real_budget = bool(
                db.execute(text("SELECT to_regclass('usage_reservations')")).scalar_one()
            )
        if has_real_budget:
            deadline = monotonic() + 8
            reservation = None
            while monotonic() < deadline:
                with factory() as db:
                    reservation = db.execute(
                        text("SELECT state FROM usage_reservations WHERE tenant_id=:tenant"),
                        {"tenant": principal.tenant_id},
                    ).scalar_one_or_none()
                if reservation == "succeeded":
                    break
                sleep(0.05)
            assert reservation == "succeeded"
    assert not app.state.ai_runner._thread.is_alive()


def test_application_runner_and_manual_handoff_preserve_lock_order(scenario, monkeypatch):
    from time import monotonic, sleep

    from fastapi.testclient import TestClient

    from ips.main import create_app
    from ips.tickets import locks

    factory, principal, conversation, tickets = scenario
    user_id, membership_id = uuid4(), uuid4()
    with factory.begin() as db:
        db.execute(
            text("INSERT INTO users (id,normalized_email,password_hash) VALUES (:id,:email,'x')"),
            {"id": user_id, "email": f"{user_id}@example.org"},
        )
        db.execute(
            text(
                "INSERT INTO memberships (id,tenant_id,user_id,role) "
                "VALUES (:id,:tenant,:user,'specialist')"
            ),
            {"id": membership_id, "tenant": principal.tenant_id, "user": user_id},
        )
    entered = Event()
    original_guard = locks.lock_handoff_memberships

    def guarded(db, tenant_id):
        entered.set()
        original_guard(db, tenant_id)

    monkeypatch.setattr(locks, "lock_handoff_memberships", guarded)
    app = create_app(Settings(ai_runner_enabled=True))
    try:
        with factory.begin() as db:
            db.execute(
                text("SELECT id FROM memberships WHERE id=:id FOR UPDATE"), {"id": membership_id}
            )
            with TestClient(app):
                assert entered.wait(8)
                tickets.escalate(db, principal, conversation, "Manual", None, "normal")
                db.commit()
                deadline = monotonic() + 8
                while monotonic() < deadline:
                    with factory() as inspect_db:
                        state = inspect_db.execute(
                            text("SELECT state FROM ai_runs WHERE conversation_id=:id"),
                            {"id": conversation},
                        ).scalar_one()
                    if state in ("discarded", "failed", "completed"):
                        break
                    sleep(0.05)
                assert state == "discarded"
                assert assistants(factory, principal) == 0
                with factory() as inspect_db:
                    count = inspect_db.execute(
                        text("SELECT count(*) FROM tickets WHERE tenant_id=:tenant"),
                        {"tenant": principal.tenant_id},
                    ).scalar_one()
                    assert count == 1
    finally:
        with factory.begin() as db:
            db.execute(
                text("UPDATE tickets SET assignee_id=NULL WHERE tenant_id=:tenant"),
                {"tenant": principal.tenant_id},
            )
            db.execute(text("DELETE FROM memberships WHERE id=:id"), {"id": membership_id})
            db.execute(text("DELETE FROM users WHERE id=:id"), {"id": user_id})


def test_application_shutdown_stops_worker_waiting_for_database_lock(scenario, monkeypatch):
    from fastapi.testclient import TestClient

    from ips.main import create_app
    from ips.tickets import locks

    factory, principal, conversation, _ = scenario
    user, membership = uuid4(), uuid4()
    with factory.begin() as db:
        db.execute(
            text("INSERT INTO users (id,normalized_email,password_hash) VALUES (:id,:email,'x')"),
            {"id": user, "email": f"{user}@example.org"},
        )
        db.execute(
            text("""INSERT INTO memberships (id,tenant_id,user_id,role)
            VALUES (:id,:tenant,:user,'specialist')"""),
            {"id": membership, "tenant": principal.tenant_id, "user": user},
        )
    entered, shutdown = Event(), Event()
    original_guard = locks.lock_handoff_memberships

    def signaling_guard(db, tenant_id):
        entered.set()
        original_guard(db, tenant_id)

    monkeypatch.setattr(locks, "lock_handoff_memberships", signaling_guard)
    app = create_app(Settings(ai_runner_enabled=True))

    def run_application():
        with TestClient(app):
            assert shutdown.wait(5)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool, factory.begin() as db:
            db.execute(
                text("SELECT id FROM memberships WHERE id=:id FOR UPDATE"), {"id": membership}
            )
            application = pool.submit(run_application)
            assert entered.wait(5)
            shutdown.set()
            application.result(timeout=5)
            assert not app.state.ai_runner._thread.is_alive()
            state = db.execute(
                text("SELECT state FROM ai_runs WHERE conversation_id=:id"),
                {"id": conversation},
            ).scalar_one()
            assert state == "running"
    finally:
        with factory.begin() as db:
            db.execute(
                text("UPDATE tickets SET assignee_id=NULL WHERE tenant_id=:tenant"),
                {"tenant": principal.tenant_id},
            )
            db.execute(text("DELETE FROM memberships WHERE id=:id"), {"id": membership})
            db.execute(text("DELETE FROM users WHERE id=:id"), {"id": user})


def test_demo_runner_uses_configured_budget(scenario):
    from time import monotonic, sleep

    from fastapi.testclient import TestClient

    from ips.core.composition import default_ports
    from ips.main import create_app

    factory, _, conversation, _ = scenario
    ports = default_ports()
    budget = Budget()
    ports.usage_budget = budget
    app = create_app(Settings(ai_runner_enabled=True), ports=ports)
    with TestClient(app):
        deadline = monotonic() + 8
        while monotonic() < deadline and not budget.settlements:
            sleep(0.05)
        assert budget.settlements == [False]
        with factory() as db:
            assert (
                db.execute(
                    text("SELECT state FROM ai_runs WHERE conversation_id=:id"),
                    {"id": conversation},
                ).scalar_one()
                == "completed"
            )


def test_runner_rejects_unintegrated_provider():
    from ips.main import create_app

    with pytest.raises(ValueError, match="only the fake"):
        create_app(Settings(ai_runner_enabled=True, ai_provider="openai"))


def test_guardrails_fall_back_to_handoff_without_public_sources(scenario):
    from ips.tickets.locks import lock_handoff_memberships

    factory, principal, conversation, tickets = scenario
    provider = Provider()
    worker = Orchestrator(
        factory,
        Budget(),
        PostgresKnowledgeSearch(factory),
        provider,
        tickets=tickets,
        tenant_id=principal.tenant_id,
        handoff_guard=lock_handoff_memberships,
    )
    assert worker.run_once()
    assert not provider.started.is_set()
    with factory() as db:
        mode = db.execute(
            text("SELECT mode FROM conversations WHERE tenant_id=:tenant AND id=:conversation"),
            {"tenant": principal.tenant_id, "conversation": conversation},
        ).scalar_one()
    assert mode == "human"
    assert assistants(factory, principal) == 1


def test_real_guardrails_recheck_sources_before_the_provider(scenario, public_source):
    factory, principal, _, _ = scenario
    with factory.begin() as db:
        internal = KnowledgeRepository().create_article(
            db,
            principal.tenant_id,
            uuid4(),
            ArticleInput(
                section_id=public_source["section_id"],
                title="Private source",
                body="Internal evidence",
                visibility="internal",
                status="published",
            ),
        )

    class CandidateSearch:
        def search(self, tenant_id, widget_id, query, audience, limit=5):
            assert (tenant_id, widget_id, audience) == (
                principal.tenant_id,
                principal.widget_id,
                "customer",
            )
            return [
                KnowledgeSnippet(
                    article_id=article["id"],
                    revision=1,
                    title="Candidate title",
                    text="Candidate text",
                    visibility="public",
                )
                for article in (public_source, internal)
            ]

    class CapturingProvider(Provider):
        def answer(self, data, timeout_seconds):
            self.data = data
            return super().answer(data, timeout_seconds)

    provider = CapturingProvider()
    worker = Orchestrator(
        factory, Budget(), CandidateSearch(), provider, tenant_id=principal.tenant_id
    )
    assert worker.run_once()
    assert provider.started.is_set()
    [snippet] = provider.data.snippets
    assert snippet["article_id"] == str(public_source["id"])
    assert snippet["title"] == "Public source"
    assert "Candidate text" not in snippet["text"]
    assert assistants(factory, principal) == 1


@pytest.mark.parametrize("revoke", ["article", "widget_binding"])
def test_answer_is_discarded_when_source_access_changes(scenario, search, public_source, revoke):
    factory, principal, _, _ = scenario
    provider = Provider(pause=True)
    worker = Orchestrator(factory, Budget(), search, provider, tenant_id=principal.tenant_id)
    tenant = {"tenant": principal.tenant_id}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker.run_once)
        try:
            assert provider.started.wait(5)
            with factory.begin() as db:
                if revoke == "article":
                    db.execute(
                        text(
                            "UPDATE articles SET visibility='internal' WHERE tenant_id=:tenant AND id=:id"
                        ),
                        {**tenant, "id": public_source["id"]},
                    )
                else:
                    db.execute(
                        text(
                            "DELETE FROM widget_sections WHERE tenant_id=:tenant AND widget_id=:widget"
                        ),
                        {**tenant, "widget": principal.widget_id},
                    )
        finally:
            provider.resume.set()
        assert future.result(timeout=5)

    assert assistants(factory, principal) == 0
    with factory() as db:
        run = db.execute(
            text("SELECT state,error_code FROM ai_runs WHERE tenant_id=:tenant"), tenant
        ).one()
    assert run == ("discarded", "source_unavailable")
