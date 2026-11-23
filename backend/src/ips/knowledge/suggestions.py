"""Human-reviewed knowledge suggestions from resolved tickets."""

import json
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import WithJsonSchema
from sqlalchemy import text
from sqlalchemy.orm import Session

from ips.core.db import get_session
from ips.core.ports import DTO, TenantContext
from ips.knowledge.dedup import fingerprint, redact
from ips.knowledge.repository import KnowledgeRepository
from ips.knowledge.schemas import ArticleInput
from ips.platform.auth import require_csrf, require_role, tenant_context
from ips.platform.members import cursor_value
from ips.platform.schemas import ErrorResponse

errors: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 429, 503)
}
router = APIRouter(prefix="/api/v1", tags=["C"], responses=errors)
admin = require_role("admin")
Cursor = Annotated[str | None, WithJsonSchema({"type": "string"})]


class Suggestion(DTO):
    id: UUID
    ticket_id: UUID
    target_article_id: UUID | None
    text: str
    state: Literal["pending", "approved", "rejected"]
    pii_flags: list[str]


class SuggestionPage(DTO):
    items: list[Suggestion]
    next_cursor: str | None


class SuggestionDecision(DTO):
    decision: Literal["approve", "reject"]


def _item(row):
    return Suggestion(
        id=row["id"],
        ticket_id=row["ticket_id"],
        target_article_id=row["target_article_id"],
        text=row["draft_text"],
        state=row["state"],
        pii_flags=row["pii_flags"],
    )


@router.post(
    "/tickets/{ticket_id}/knowledge-suggestions",
    operation_id="suggestKnowledge",
    status_code=201,
    response_model=Suggestion,
    dependencies=[Depends(require_csrf)],
)
def suggest(
    ticket_id: UUID,
    ctx: Annotated[TenantContext, Depends(tenant_context)],
    db: Annotated[Session, Depends(get_session)],
):
    ticket = (
        db.execute(
            text(
                "SELECT conversation_id,status FROM tickets WHERE tenant_id=:tenant AND id=:id FOR UPDATE"
            ),
            {"tenant": ctx.tenant_id, "id": ticket_id},
        )
        .mappings()
        .first()
    )
    if ticket is None:
        raise HTTPException(404, "Ticket unavailable")
    if ticket["status"] != "resolved":
        raise HTTPException(409, "Ticket must be resolved")
    rows = (
        db.execute(
            text(
                "SELECT text FROM messages WHERE tenant_id=:tenant AND conversation_id=:conversation AND kind IN ('customer','specialist','assistant') ORDER BY seq LIMIT 100"
            ),
            {"tenant": ctx.tenant_id, "conversation": ticket["conversation_id"]},
        )
        .scalars()
        .all()
    )
    if not rows:
        raise HTTPException(409, "No source messages")
    draft, flags = redact("\n".join(rows))
    draft = draft[:50000]
    key = fingerprint(draft)
    existing = (
        db.execute(
            text(
                "SELECT * FROM knowledge_suggestions WHERE tenant_id=:tenant AND ticket_id=:ticket AND fingerprint=:key"
            ),
            {"tenant": ctx.tenant_id, "ticket": ticket_id, "key": key},
        )
        .mappings()
        .first()
    )
    if existing:
        return _item(existing)
    # Only exact normalized matches become update targets; fuzzy matches need human choice.
    articles = db.execute(
        text(
            """SELECT a.id,r.body FROM articles a JOIN article_revisions r ON r.tenant_id=a.tenant_id AND r.article_id=a.id AND r.id=a.current_revision_id WHERE a.tenant_id=:tenant ORDER BY a.id"""
        ),
        {"tenant": ctx.tenant_id},
    ).mappings()
    target = next((item["id"] for item in articles if fingerprint(item["body"]) == key), None)
    row = (
        db.execute(
            text("""INSERT INTO knowledge_suggestions (id,tenant_id,ticket_id,target_article_id,draft_text,fingerprint,pii_flags)
        VALUES (:id,:tenant,:ticket,:target,:draft,:key,CAST(:flags AS jsonb)) RETURNING *"""),
            {
                "id": uuid4(),
                "tenant": ctx.tenant_id,
                "ticket": ticket_id,
                "target": target,
                "draft": draft,
                "key": key,
                "flags": json.dumps(flags),
            },
        )
        .mappings()
        .one()
    )
    result = _item(row)
    db.commit()
    return result


@router.get(
    "/knowledge/suggestions", operation_id="listKnowledgeSuggestions", response_model=SuggestionPage
)
def list_suggestions(
    ctx: Annotated[TenantContext, Depends(admin)],
    db: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: Cursor = None,
):
    rows = (
        db.execute(
            text(
                "SELECT * FROM knowledge_suggestions WHERE tenant_id=:tenant AND id>:after ORDER BY id LIMIT :limit"
            ),
            {"tenant": ctx.tenant_id, "after": cursor_value(cursor), "limit": limit + 1},
        )
        .mappings()
        .all()
    )
    return SuggestionPage(
        items=[_item(row) for row in rows[:limit]],
        next_cursor=str(rows[limit - 1]["id"]) if len(rows) > limit else None,
    )


@router.post(
    "/knowledge/suggestions/{suggestion_id}/decision",
    operation_id="decideKnowledgeSuggestion",
    response_model=Suggestion,
    dependencies=[Depends(require_csrf)],
)
def decide(
    suggestion_id: UUID,
    body: SuggestionDecision,
    ctx: Annotated[TenantContext, Depends(admin)],
    db: Annotated[Session, Depends(get_session)],
):
    params = {"tenant": ctx.tenant_id, "id": suggestion_id}
    row = (
        db.execute(
            text(
                "SELECT * FROM knowledge_suggestions WHERE tenant_id=:tenant AND id=:id FOR UPDATE"
            ),
            params,
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, "Suggestion unavailable")
    if row["state"] != "pending":
        raise HTTPException(409, "Suggestion already decided")
    target = row["target_article_id"]
    if body.decision == "approve":
        repo = KnowledgeRepository()
        if target:
            article = repo.lock_article(db, ctx.tenant_id, target)
            if article is None:
                raise HTTPException(404, "Article unavailable")
            repo.save_revision(
                db,
                article,
                {"body": row["draft_text"], "status": "draft"},
                ctx.user_id,
                "suggestion",
            )
        else:
            section = db.execute(
                text("SELECT id FROM article_sections WHERE tenant_id=:tenant ORDER BY id LIMIT 1"),
                params,
            ).scalar_one_or_none()
            if section is None:
                raise HTTPException(409, "Create a knowledge section first")
            article = repo.create_article(
                db,
                ctx.tenant_id,
                ctx.user_id,
                ArticleInput(
                    section_id=section,
                    title="Предложение из заявки",
                    body=row["draft_text"],
                    visibility="internal",
                    status="draft",
                ),
                source_type="suggestion",
                source_id=suggestion_id,
            )
            target = article["id"]
    updated = (
        db.execute(
            text(
                """UPDATE knowledge_suggestions SET state=:state,target_article_id=:target WHERE tenant_id=:tenant AND id=:id RETURNING *"""
            ),
            {
                **params,
                "target": target,
                "state": "approved" if body.decision == "approve" else "rejected",
            },
        )
        .mappings()
        .one()
    )
    result = _item(updated)
    db.commit()
    return result
