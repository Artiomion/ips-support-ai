"""Wire the currently implemented domain ports without simulating missing features."""

from types import SimpleNamespace

from fastapi import HTTPException

from ips.conversations.repository import ConversationRepository
from ips.conversations.service import ConversationService
from ips.tickets.service import TicketService


class ConversationAdapter(ConversationService):
    def confirm_human(self, uow, tenant_id, conversation_id):
        # C07 owns outcome persistence; fail closed until it is implemented.
        raise HTTPException(503, "Conversation outcome unavailable")

    def metrics(self, uow, ctx, widget_id):
        raise HTTPException(503, "Conversation metrics unavailable")


def default_ports():
    conversations = ConversationAdapter(ConversationRepository())
    return SimpleNamespace(conversations=conversations, tickets=TicketService(conversations))
