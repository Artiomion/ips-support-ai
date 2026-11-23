"""Wire the currently implemented domain ports without simulating missing features."""

from types import SimpleNamespace

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
