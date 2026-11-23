"""Wire the currently implemented domain ports without simulating missing features."""

from types import SimpleNamespace
from uuid import uuid4

from ips.conversations.events import QueuedStaffReplyNotifications
from ips.conversations.outcomes import OutcomeConversationService
from ips.conversations.repository import ConversationRepository
from ips.tickets.service import TicketService


def default_ports():
    conversations = OutcomeConversationService(ConversationRepository())
    tickets = TicketService(conversations)
    conversations.tickets = tickets
    return SimpleNamespace(
        conversations=conversations, tickets=tickets, notifications=QueuedStaffReplyNotifications()
    )


def demo_ai_runner(engine, ports):
    """Compose the explicitly enabled offline demonstration worker.

    Use B06's quota service when available; otherwise the demo is unmetered.
    """
    from sqlalchemy.orm import sessionmaker

    from ips.ai.fake import FakeLLMProvider
    from ips.ai.orchestrator import Orchestrator
    from ips.ai.runner import AIRunner
    from ips.core.ports import Reservation
    from ips.knowledge.retrieval import PostgresKnowledgeSearch
    from ips.tickets.locks import lock_handoff_memberships

    class DemoBudget:
        def reserve(self, tenant_id, run_id):
            return Reservation(id=uuid4(), tenant_id=tenant_id, run_id=run_id)

        def settle(self, reservation, usage, failed):
            pass

    sessions = sessionmaker(engine)
    worker = Orchestrator(
        sessions,
        getattr(ports, "usage_budget", None) or DemoBudget(),
        PostgresKnowledgeSearch(sessions),
        FakeLLMProvider(),
        tickets=ports.tickets,
        handoff_guard=lock_handoff_memberships,
        demo=True,
    )
    return AIRunner(worker, engine)
