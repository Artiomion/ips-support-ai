"""Shared transport-independent DTOs and ports for the B/C domain boundary."""

from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TenantContext(DTO):
    tenant_id: UUID
    user_id: UUID
    membership_id: UUID
    role: Literal["admin", "specialist"]


class CustomerPrincipal(DTO):
    tenant_id: UUID
    customer_id: UUID
    widget_id: UUID
    conversation_scope: UUID | None = None


class Citation(DTO):
    article_id: UUID
    revision: int = Field(ge=1)
    title: str


class ImageInput(DTO):
    attachment_id: UUID
    mime: str
    data: bytes


class AnswerInput(DTO):
    text: str
    history: list[dict[str, Any]]
    snippets: list[dict[str, Any]]
    images: list[ImageInput] = Field(default_factory=list)


class AnswerResult(DTO):
    text: str
    citations: list[Citation]
    needs_human: bool
    category: str | None
    priority: Literal["low", "normal", "high", "urgent"]
    reason: str | None


class ConversationState(DTO):
    id: UUID
    tenant_id: UUID
    customer_id: UUID
    widget_id: UUID
    mode: Literal["bot", "human"]
    version: int


class ConversationCounts(DTO):
    conversations: int
    ai_confirmed: int
    unconfirmed: int
    handoffs: int


class TicketSummary(DTO):
    id: UUID
    conversation_id: UUID
    status: Literal[
        "new", "waiting_specialist", "in_progress", "waiting_customer", "resolved", "closed"
    ]
    priority: Literal["low", "normal", "high", "urgent"]
    category_id: UUID | None
    assignee_id: UUID | None
    version: int
    created_at: datetime
    updated_at: datetime


class StaffMessage(DTO):
    id: UUID
    conversation_id: UUID
    seq: int
    kind: Literal["customer", "assistant", "specialist", "internal_note"]
    text: str
    sources: list[Citation]
    created_at: datetime
    attachment_ids: list[UUID] = Field(default_factory=list)


class StaffReplyEvent(DTO):
    tenant_id: UUID
    conversation_id: UUID
    message_id: UUID
    created_at: datetime


class KnowledgeSnippet(DTO):
    article_id: UUID
    revision: int
    title: str
    text: str
    visibility: Literal["public", "internal"]


class Reservation(DTO):
    id: UUID
    tenant_id: UUID
    run_id: UUID


class Usage(DTO):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


@runtime_checkable
class TicketPort(Protocol):
    def escalate(
        self,
        uow: Session,
        principal: CustomerPrincipal,
        conversation_id: UUID,
        reason: str,
        category: str | None,
        priority: str,
    ) -> TicketSummary: ...
    def on_customer_message(
        self, uow: Session, principal: CustomerPrincipal, conversation_id: UUID
    ) -> None: ...
    def has_active(self, uow: Session, tenant_id: UUID, conversation_id: UUID) -> bool: ...
    def record_staff_reply(
        self, uow: Session, ctx: TenantContext, conversation_id: UUID, created_at: datetime
    ) -> None: ...


@runtime_checkable
class ConversationPort(Protocol):
    def lock_for_handoff(
        self, uow: Session, tenant_id: UUID, conversation_id: UUID
    ) -> ConversationState: ...
    def mark_human(self, uow: Session, state: ConversationState) -> None: ...
    def add_staff_message(
        self,
        uow: Session,
        ctx: TenantContext,
        conversation_id: UUID,
        text: str,
        internal: bool,
        client_message_id: UUID,
    ) -> StaffMessage: ...
    def metrics(
        self, uow: Session, ctx: TenantContext, widget_id: UUID | None
    ) -> ConversationCounts: ...
    def confirm_human(self, uow: Session, tenant_id: UUID, conversation_id: UUID) -> None: ...


@runtime_checkable
class NotificationPort(Protocol):
    def enqueue_staff_reply(self, uow: Session, event: StaffReplyEvent) -> None: ...


@runtime_checkable
class KnowledgeSearch(Protocol):
    def search(
        self,
        tenant_id: UUID,
        widget_id: UUID,
        query: str,
        audience: Literal["customer", "staff"],
        limit: int = 5,
    ) -> list[KnowledgeSnippet]: ...


@runtime_checkable
class LLMProvider(Protocol):
    def answer(self, data: AnswerInput, timeout_seconds: float) -> AnswerResult: ...


@runtime_checkable
class UsageBudget(Protocol):
    def reserve(self, tenant_id: UUID, run_id: UUID) -> Reservation: ...
    def settle(self, reservation: Reservation, usage: Usage, failed: bool) -> None: ...
