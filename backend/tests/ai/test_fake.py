from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from ips.ai.fake import FakeLLMProvider
from ips.ai.provider import AnswerInput, AnswerResult, LLMProvider
from ips.conversations.schemas import PublicMessage
from ips.core.ports import Reservation, TicketPort, TicketSummary, Usage, UsageBudget
from ips.knowledge.schemas import ArticleInput


class FakeTicketPort:
    def __init__(self):
        self.tickets = {}
        self.replies = []

    def escalate(self, uow, principal, conversation_id, reason, category, priority):
        key = (principal.tenant_id, conversation_id)
        if key not in self.tickets:
            now = datetime.now(UTC)
            self.tickets[key] = TicketSummary(
                id=uuid4(),
                conversation_id=conversation_id,
                status="waiting_specialist",
                priority=priority,
                category_id=None,
                assignee_id=None,
                version=1,
                created_at=now,
                updated_at=now,
            )
        return self.tickets[key]

    def on_customer_message(self, uow, principal, conversation_id):
        pass

    def has_active(self, uow, tenant_id, conversation_id):
        return (tenant_id, conversation_id) in self.tickets

    def record_staff_reply(self, uow, ctx, conversation_id, created_at):
        self.replies.append((ctx.tenant_id, conversation_id, created_at))


class FakeUsageBudget:
    def __init__(self):
        self.reservations = {}
        self.settlements = {}

    def reserve(self, tenant_id, run_id):
        key = (tenant_id, run_id)
        if key not in self.reservations:
            self.reservations[key] = Reservation(id=uuid4(), tenant_id=tenant_id, run_id=run_id)
        return self.reservations[key]

    def settle(self, reservation, usage, failed):
        self.settlements[reservation.id] = (usage, failed)


def test_fake_ports_implement_shared_contract():
    tickets, budget = FakeTicketPort(), FakeUsageBudget()
    assert isinstance(tickets, TicketPort)
    assert isinstance(budget, UsageBudget)
    tenant_id, run_id = uuid4(), uuid4()
    reservation = budget.reserve(tenant_id, run_id)
    assert budget.reserve(tenant_id, run_id) == reservation
    usage = Usage(input_tokens=5, output_tokens=2)
    budget.settle(reservation, usage, failed=False)
    assert budget.settlements[reservation.id] == (usage, False)


def test_fake_is_deterministic_and_does_not_use_network(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("fake provider must not use the network")

    monkeypatch.setattr("socket.socket.connect", no_network)
    article_id = uuid4()
    data = AnswerInput(
        text="refund",
        history=[],
        snippets=[
            {
                "article_id": str(article_id),
                "revision": 1,
                "title": "Refunds",
                "text": "Refunds are available.",
                "visibility": "public",
            }
        ],
    )
    provider = FakeLLMProvider()
    first = provider.answer(data, timeout_seconds=20)
    assert isinstance(provider, LLMProvider)
    assert first == provider.answer(data, timeout_seconds=20)
    assert not first.needs_human
    assert first.citations[0].article_id == article_id


def test_unknown_question_and_internal_snippet_need_human():
    data = AnswerInput(
        text="secret",
        history=[],
        snippets=[
            {
                "article_id": str(uuid4()),
                "revision": 1,
                "title": "Internal",
                "text": "secret",
                "visibility": "internal",
            }
        ],
    )
    result = FakeLLMProvider().answer(data, timeout_seconds=20)
    assert result.needs_human
    assert not result.citations
    assert "secret" not in result.text


def test_bad_output_and_invalid_domain_data_are_rejected():
    with pytest.raises(ValidationError):
        AnswerResult(
            text="bad",
            citations=[],
            needs_human=False,
            category=None,
            priority="invalid",
            reason=None,
        )
    with pytest.raises(ValidationError):
        ArticleInput(
            section_id=uuid4(), title="", body="body", visibility="public", status="published"
        )
    with pytest.raises(ValidationError):
        PublicMessage(
            id=uuid4(),
            conversation_id=uuid4(),
            seq=1,
            kind="internal_note",
            text="secret",
            sources=[],
            created_at="2026-01-01T00:00:00Z",
        )
