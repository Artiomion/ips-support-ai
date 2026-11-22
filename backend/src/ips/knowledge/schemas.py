"""Knowledge DTOs matching the v1 contract."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from ips.core.ports import DTO, KnowledgeSnippet

Visibility = Literal["public", "internal"]
ArticleStatus = Literal["draft", "published", "disabled"]


class Section(DTO):
    id: UUID
    name: str


class Article(DTO):
    id: UUID
    section_id: UUID
    title: str
    body: str
    visibility: Visibility
    status: ArticleStatus
    revision: int = Field(ge=1)


class ArticleInput(DTO):
    section_id: UUID
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=100000)
    visibility: Visibility
    status: ArticleStatus


class ArticleRevision(DTO):
    revision: int = Field(ge=1)
    title: str
    body: str
    created_at: datetime
    source_type: str


METADATA_MODELS: tuple[type, ...] = ()

__all__ = [
    "METADATA_MODELS",
    "Article",
    "ArticleInput",
    "ArticleRevision",
    "KnowledgeSnippet",
    "Section",
]
