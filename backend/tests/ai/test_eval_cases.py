"""Deterministic C08 scenarios against the real PostgreSQL domain pipeline."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from ips.ai.fake import FakeLLMProvider
from ips.ai.orchestrator import Orchestrator
from ips.conversations.repository import ConversationRepository
from ips.conversations.service import ConversationService
from ips.core.composition import default_ports
from ips.core.config import Settings
from ips.core.ports import CustomerPrincipal, Reservation
from ips.knowledge.repository import KnowledgeRepository
from ips.knowledge.retrieval import PostgresKnowledgeSearch
from ips.knowledge.schemas import ArticleInput

FIXTURES = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "ai-cases.json").read_text(encoding="utf-8")
)
CASES = FIXTURES["cases"]


class Budget:
    def __init__(self, reject=False):
        self.reject = reject
        self.settlements = []

    def reserve(self, tenant_id, run_id):
        if self.reject:
            raise RuntimeError("quota exhausted")
        return Reservation(id=uuid4(), tenant_id=tenant_id, run_id=run_id)

    def settle(self, reservation, usage, failed):
        self.settlements.append(failed)


class RecordingProvider:
    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = []
        self.started, self.resume = Event(), Event()

    def answer(self, data, timeout_seconds):
        self.calls.append(data)
        if self.behavior == "timeout":
            raise TimeoutError("synthetic timeout")
        if self.behavior == "failed_answer":
            return {"text": "invalid result", "priority": "emergency"}
        if self.behavior == "race":
            self.started.set()
            if not self.resume.wait(10):
                raise TimeoutError("test did not resume")
        return FakeLLMProvider().answer(data, timeout_seconds)


@pytest.fixture
def demo():
    engine = create_engine(Settings().database_url, connect_args={"connect_timeout": 2})
    sessions = sessionmaker(engine)
    orgs = []
    try:
        with sessions.begin() as db:
            for company in FIXTURES["companies"]:
                tenant, widget, customer, section = (uuid4() for _ in range(4))
                principal = CustomerPrincipal(
                    tenant_id=tenant, widget_id=widget, customer_id=customer
                )
                orgs.append({"principal": principal, "section": section, "articles": {}})
                db.execute(
                    text("INSERT INTO organizations (id,name) VALUES (:id,:name)"),
                    {"id": tenant, "name": company["name"]},
                )
                db.execute(
                    text("""INSERT INTO widgets
                    (id,tenant_id,name,public_key,color,greeting,allowed_origins)
                    VALUES (:id,:tenant,'test',:key,'#ffffff','hello',ARRAY['https://example.test'])"""),
                    {"id": widget, "tenant": tenant, "key": str(widget)},
                )
                db.execute(
                    text("INSERT INTO customers (id,tenant_id) VALUES (:id,:tenant)"),
                    {"id": customer, "tenant": tenant},
                )
                db.execute(
                    text(
                        "INSERT INTO article_sections (id,tenant_id,name) VALUES (:id,:tenant,:name)"
                    ),
                    {"id": section, "tenant": tenant, "name": company["name"]},
                )
                db.execute(
                    text(
                        "INSERT INTO widget_sections (tenant_id,widget_id,section_id) VALUES (:tenant,:widget,:section)"
                    ),
                    {"tenant": tenant, "widget": widget, "section": section},
                )
                for article in company["articles"]:
                    row = KnowledgeRepository().create_article(
                        db,
                        tenant,
                        uuid4(),
                        ArticleInput(
                            section_id=section,
                            title=article["title"],
                            body=article["body"],
                            visibility=article["visibility"],
                            status="published",
                        ),
                    )
                    orgs[-1]["articles"][article["title"]] = row["id"]
        yield sessions, orgs
    finally:
        if orgs:
            with sessions.begin() as db:
                for company in orgs:
                    tenant = company["principal"].tenant_id
                    params = {"tenant": tenant}
                    for table in (
                        "jobs",
                        "tickets",
                        "ai_runs",
                        "messages",
                        "conversations",
                        "widget_sections",
                        "customers",
                        "widgets",
                    ):
                        db.execute(text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), params)
                    db.execute(
                        text(
                            "UPDATE articles SET current_revision_id=NULL WHERE tenant_id=:tenant"
                        ),
                        params,
                    )
                    for table in ("article_revisions", "articles", "article_sections"):
                        db.execute(text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), params)
                    db.execute(text("DELETE FROM organizations WHERE id=:tenant"), params)
        engine.dispose()


def test_fixture_covers_each_required_behavior_once():
    assert len(CASES) == 12
    assert {case["id"] for case in CASES} == {
        "known",
        "unknown",
        "ambiguity",
        "human_request",
        "failed_answer",
        "urgency",
        "injection",
        "private",
        "cross_tenant",
        "timeout",
        "quota",
        "race",
    }
    assert len(FIXTURES["companies"]) == 2


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_eval_cases(demo, case):
    sessions, companies = demo
    alpha, beta = companies
    principal = alpha["principal"]
    ports = default_ports()
    service = ConversationService(ConversationRepository())
    provider = RecordingProvider(case["id"])
    budget = Budget(reject=case["id"] == "quota")
    search = PostgresKnowledgeSearch(sessions)
    with sessions.begin() as db:
        conversation = service.repository.create(db, principal)["id"]
        service.send_customer(db, principal, conversation, uuid4(), case["question"])
        if case["id"] == "human_request":
            ports.tickets.escalate(db, principal, conversation, "customer request", None, "normal")
    worker = Orchestrator(
        sessions, budget, search, provider, tickets=ports.tickets, tenant_id=principal.tenant_id
    )
    if case["id"] == "race":
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(worker.run_once)
            try:
                assert provider.started.wait(10)
                with sessions.begin() as db:
                    ports.tickets.escalate(
                        db, principal, conversation, "customer request", None, "normal"
                    )
            finally:
                provider.resume.set()
            assert future.result(timeout=10)
    else:
        assert worker.run_once()
    with sessions() as db:
        run = (
            db.execute(
                text(
                    "SELECT state,error_code FROM ai_runs WHERE tenant_id=:tenant AND conversation_id=:id"
                ),
                {"tenant": principal.tenant_id, "id": conversation},
            )
            .mappings()
            .one()
        )
        messages = (
            db.execute(
                text(
                    "SELECT text,sources FROM messages WHERE tenant_id=:tenant AND conversation_id=:id AND kind='assistant'"
                ),
                {"tenant": principal.tenant_id, "id": conversation},
            )
            .mappings()
            .all()
        )
        mode = db.execute(
            text("SELECT mode FROM conversations WHERE tenant_id=:tenant AND id=:id"),
            {"tenant": principal.tenant_id, "id": conversation},
        ).scalar_one()
        active = ports.tickets.has_active(db, principal.tenant_id, conversation)
    if case["expected"] == "answer_with_source":
        assert run["state"] == "completed" and mode == "bot" and not active
        assert len(messages) == 1 and "refund" in messages[0]["text"]
        assert [str(s["article_id"]) for s in messages[0]["sources"]] == [
            str(alpha["articles"]["Alpha returns"])
        ]
    elif case["expected"] == "human_fallback":
        assert run["state"] in ("completed", "discarded") and mode == "human" and active
        assert all(not m["sources"] for m in messages)
        assert "alphasecret" not in str(messages) and "betasecret" not in str(messages)
        assert all(
            "alphasecret" not in str(call.model_dump())
            and "betasecret" not in str(call.model_dump())
            for call in provider.calls
        )
    elif case["expected"] == "failed_manual_handoff":
        assert run["state"] == "failed" and not messages and not active
        with sessions.begin() as db:
            ports.tickets.escalate(db, principal, conversation, "manual help", None, "normal")
            assert ports.tickets.has_active(db, principal.tenant_id, conversation)
        assert budget.settlements == ([] if case["id"] == "quota" else [True])
    else:
        assert case["expected"] == "discard_after_handoff"
        assert run["state"] == "discarded" and not messages and mode == "human" and active
    if case["id"] == "cross_tenant":
        assert (
            search.search(principal.tenant_id, principal.widget_id, "betasecret", "customer") == []
        )
        assert (
            len(
                search.search(
                    beta["principal"].tenant_id,
                    beta["principal"].widget_id,
                    "betasecret",
                    "customer",
                )
            )
            == 1
        )
    if case["id"] in ("private", "cross_tenant", "injection", "quota", "human_request"):
        assert not provider.calls
