"""Separate, offline staff assistant; never writes to customer messages."""

import json
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from ips.core.db import get_session
from ips.core.ports import DTO, Citation, TenantContext
from ips.platform.auth import require_csrf, tenant_context
from ips.platform.schemas import ErrorResponse

errors: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 429, 503)
}
router = APIRouter(prefix="/api/v1", tags=["C"], responses=errors)


class AssistantInput(DTO):
    text: str = Field(min_length=1, max_length=8000)


class AssistantAnswer(DTO):
    text: str = Field(min_length=1, max_length=8000)
    sources: list[Citation]


@router.post(
    "/tickets/{ticket_id}/assistant",
    operation_id="askTicketAssistant",
    response_model=AssistantAnswer,
    dependencies=[Depends(require_csrf)],
)
def ask_assistant(
    ticket_id: UUID,
    body: AssistantInput,
    ctx: Annotated[TenantContext, Depends(tenant_context)],
    db: Annotated[Session, Depends(get_session)],
):
    ticket = db.execute(
        text("SELECT conversation_id FROM tickets WHERE tenant_id=:tenant AND id=:id"),
        {"tenant": ctx.tenant_id, "id": ticket_id},
    ).scalar_one_or_none()
    if ticket is None:
        raise HTTPException(404, "Ticket unavailable")
    rows = (
        db.execute(
            text("""SELECT kind,text FROM messages WHERE tenant_id=:tenant AND conversation_id=:conversation
        ORDER BY seq DESC LIMIT 10"""),
            {"tenant": ctx.tenant_id, "conversation": ticket},
        )
        .mappings()
        .all()
    )
    sources = (
        db.execute(
            text("""SELECT a.id AS article_id,r.version AS revision,r.title,r.body
        FROM articles a JOIN article_revisions r ON r.tenant_id=a.tenant_id AND r.article_id=a.id AND r.id=a.current_revision_id
        WHERE a.tenant_id=:tenant AND a.status='published' AND strpos(lower(r.title || ' ' || r.body),lower(:query))>0
        ORDER BY a.id LIMIT 3"""),
            {"tenant": ctx.tenant_id, "query": body.text.strip()[:200]},
        )
        .mappings()
        .all()
        if body.text.strip()
        else []
    )
    transcript = (
        "\n".join(f"{row['kind']}: {row['text']}" for row in reversed(rows))[:3500]
        or "Нет сообщений."
    )
    material = (
        "\n".join(f"{row['title']}: {row['body'][:500]}" for row in sources)
        or "Подходящие материалы не найдены."
    )
    answer = f"Демонстрационный режим. Краткое содержание:\n{transcript}\n\nМатериалы:\n{material}\n\nЧерновик: Здравствуйте! Мы изучили ваш вопрос. [Добавьте проверенное решение перед отправкой.]"[
        :8000
    ]
    citations = [
        Citation(article_id=row["article_id"], revision=row["revision"], title=row["title"])
        for row in sources
    ]
    db.execute(
        text("""INSERT INTO staff_assistant_messages (id,tenant_id,ticket_id,staff_id,question,answer,sources)
        VALUES (:id,:tenant,:ticket,:staff,:question,:answer,CAST(:sources AS jsonb))"""),
        {
            "id": uuid4(),
            "tenant": ctx.tenant_id,
            "ticket": ticket_id,
            "staff": ctx.user_id,
            "question": body.text,
            "answer": answer,
            "sources": json.dumps([c.model_dump(mode="json") for c in citations]),
        },
    )
    db.commit()
    return AssistantAnswer(text=answer, sources=citations)
