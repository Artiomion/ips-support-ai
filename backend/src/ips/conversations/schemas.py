"""Conversation DTOs matching the public v1 contract."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from ips.core.ports import DTO, Citation, StaffMessage


class Conversation(DTO):
    id: UUID
    widget_id: UUID
    mode: Literal["bot", "human"]
    outcome: Literal["unconfirmed", "ai_confirmed", "human_confirmed"]
    created_at: datetime
    updated_at: datetime


class CreateConversation(DTO):
    initial_text: str | None = Field(default=None, min_length=1, max_length=8000)


class PublicMessage(DTO):
    id: UUID
    conversation_id: UUID
    seq: int = Field(ge=1)
    kind: Literal["customer", "assistant", "specialist"]
    text: str = Field(min_length=1, max_length=8000)
    sources: list[Citation]
    created_at: datetime
    attachment_ids: list[UUID] = Field(default_factory=list)


class MessageInput(DTO):
    client_message_id: UUID
    text: str = Field(min_length=1, max_length=8000)
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=4)


class StaffMessageInput(DTO):
    client_message_id: UUID
    text: str = Field(min_length=1, max_length=8000)
    internal: bool


# C01 adds DTOs only; future C02 ORM models are registered by the composition root.
METADATA_MODELS: tuple[type, ...] = ()

__all__ = [
    "METADATA_MODELS",
    "Conversation",
    "CreateConversation",
    "MessageInput",
    "PublicMessage",
    "StaffMessage",
    "StaffMessageInput",
]
