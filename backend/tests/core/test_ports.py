from uuid import uuid4

import pytest
from pydantic import ValidationError

from ips.core.ports import AnswerResult, CustomerPrincipal, TicketPort


def test_customer_principal_retains_scope():
    tenant, customer, widget, conversation = (uuid4() for _ in range(4))
    principal = CustomerPrincipal(
        tenant_id=tenant, customer_id=customer, widget_id=widget, conversation_scope=conversation
    )
    assert principal.tenant_id == tenant
    assert principal.conversation_scope == conversation


def test_model_output_rejects_unknown_priority():
    with pytest.raises(ValidationError):
        AnswerResult(
            text="x",
            citations=[],
            needs_human=False,
            category=None,
            priority="critical-ish",
            reason=None,
        )


def test_ticket_port_can_be_implemented_by_fake():
    class Fake:
        def escalate(self, *args, **kwargs): ...
        def on_customer_message(self, *args, **kwargs): ...
        def has_active(self, *args, **kwargs): ...
        def record_staff_reply(self, *args, **kwargs): ...

    assert isinstance(Fake(), TicketPort)
