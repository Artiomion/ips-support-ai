from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from ips.ai.orchestrator import Orchestrator
from ips.conversations.repository import ConversationRepository
from ips.conversations.service import ConversationService
from ips.core.config import Settings
from ips.core.ports import AnswerResult, CustomerPrincipal, Reservation
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


class Search:
    def search(self, tenant_id, widget_id, query, audience, limit=5):
        assert audience == "customer"
        return []


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
            citations=[],
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
        for table in ("tickets", "ai_runs", "messages", "conversations", "customers", "widgets"):
            db.execute(text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), {"tenant": tenant})
        db.execute(text("DELETE FROM organizations WHERE id=:tenant"), {"tenant": tenant})
    engine.dispose()


def assistants(factory, principal):
    with factory() as db:
        return db.execute(
            text("SELECT count(*) FROM messages WHERE tenant_id=:tenant AND kind='assistant'"),
            {"tenant": principal.tenant_id},
        ).scalar_one()


def test_handoff_while_provider_is_running_discards_reply(scenario):
    factory, principal, conversation, tickets = scenario
    provider, budget = Provider(pause=True), Budget()
    worker = Orchestrator(factory, budget, Search(), provider, tenant_id=principal.tenant_id)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker.run_once)
        assert provider.started.wait(10)
        with factory.begin() as db:
            tickets.escalate(db, principal, conversation, "help", None, "normal")
        provider.resume.set()
        assert future.result(timeout=10)
    assert assistants(factory, principal) == 0
    assert budget.settlements == [False]


def test_duplicate_job_does_not_duplicate_answer(scenario):
    factory, principal, _, _ = scenario
    worker = Orchestrator(factory, Budget(), Search(), Provider(), tenant_id=principal.tenant_id)
    assert worker.run_once()
    assert not worker.run_once()
    assert assistants(factory, principal) == 1


def test_exhausted_budget_keeps_manual_handoff_available(scenario):
    factory, principal, conversation, tickets = scenario
    provider = Provider()
    assert Orchestrator(
        factory, Budget(reject=True), Search(), provider, tenant_id=principal.tenant_id
    ).run_once()
    assert not provider.started.is_set()
    with factory.begin() as db:
        tickets.escalate(db, principal, conversation, "help", None, "normal")
    assert assistants(factory, principal) == 0


def test_malformed_provider_result_does_not_create_ticket(scenario):
    factory, principal, _, _ = scenario
    assert Orchestrator(
        factory, Budget(), Search(), Provider(malformed=True), tenant_id=principal.tenant_id
    ).run_once()
    with factory() as db:
        count = db.execute(
            text("SELECT count(*) FROM tickets WHERE tenant_id=:tenant"),
            {"tenant": principal.tenant_id},
        ).scalar_one()
    assert count == 0
    assert assistants(factory, principal) == 0


def test_expired_lease_is_recovered_and_old_attempt_cannot_write(scenario):
    factory, principal, _, _ = scenario
    old = Provider(pause=True)
    first = Orchestrator(factory, Budget(), Search(), old, tenant_id=principal.tenant_id)
    second = Orchestrator(factory, Budget(), Search(), Provider(), tenant_id=principal.tenant_id)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(first.run_once)
        assert old.started.wait(10)
        with factory.begin() as db:
            db.execute(
                text(
                    "UPDATE ai_runs SET lease_until=now()-interval '1 second' WHERE tenant_id=:tenant"
                ),
                {"tenant": principal.tenant_id},
            )
        assert second.run_once()
        old.resume.set()
        assert future.result(timeout=10)
    assert assistants(factory, principal) == 1


def test_provider_failure_is_recorded_without_breaking_handoff(scenario):
    factory, principal, conversation, tickets = scenario

    class FailedProvider:
        def answer(self, data, timeout_seconds):
            raise RuntimeError("sensitive provider details")

    budget = Budget()
    assert Orchestrator(
        factory, budget, Search(), FailedProvider(), tenant_id=principal.tenant_id
    ).run_once()
    with factory.begin() as db:
        state, code = db.execute(
            text("SELECT state,error_code FROM ai_runs WHERE tenant_id=:tenant"),
            {"tenant": principal.tenant_id},
        ).one()
        tickets.escalate(db, principal, conversation, "help", None, "normal")
    assert (state, code) == ("failed", "generation_failed")
    assert budget.settlements == [True]


def test_valid_handoff_uses_ticket_port(scenario):
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
        Search(),
        HandoffProvider(),
        tickets=tickets,
        tenant_id=principal.tenant_id,
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
