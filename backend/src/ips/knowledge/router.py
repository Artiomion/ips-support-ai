"""Knowledge HTTP routes, registered by the application composition root."""

from pathlib import PurePosixPath
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import Field, WithJsonSchema, field_validator
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from ips.core.db import get_session
from ips.core.jobs import enqueue
from ips.core.ports import DTO, TenantContext
from ips.knowledge.imports import MAX_TXT_BYTES, ImportFailure, read_text
from ips.knowledge.parsers import MAX_DOCUMENT_BYTES
from ips.knowledge.repository import KnowledgeRepository
from ips.knowledge.schemas import Article, ArticleInput, ArticleRevision, Section
from ips.knowledge.service import KnowledgeService
from ips.platform.auth import require_csrf, require_role, tenant_context
from ips.platform.schemas import ErrorResponse

errors: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 429, 503)
}
router = APIRouter(prefix="/api/v1/knowledge", tags=["C"], responses=errors)
admin = require_role("admin")


class CategoryInput(DTO):
    name: str = Field(min_length=1, max_length=120)


class SectionPage(DTO):
    items: list[Section]
    next_cursor: str | None


class ArticlePage(DTO):
    items: list[Article]
    next_cursor: str | None


class ArticleRevisionPage(DTO):
    items: list[ArticleRevision]
    next_cursor: str | None


class ImportResult(DTO):
    id: UUID
    state: Literal["queued", "processing", "ready", "failed"]
    error_code: str | None


class ArticleUpdate(DTO):
    expected_revision: int = Field(ge=1)
    title: Annotated[
        str | None, WithJsonSchema({"type": "string", "minLength": 1, "maxLength": 200})
    ] = None
    body: Annotated[
        str | None, WithJsonSchema({"type": "string", "minLength": 1, "maxLength": 100000})
    ] = None
    visibility: Annotated[
        Literal["public", "internal"] | None,
        WithJsonSchema({"type": "string", "enum": ["public", "internal"]}),
    ] = None
    status: Annotated[
        Literal["draft", "published", "disabled"] | None,
        WithJsonSchema({"type": "string", "enum": ["draft", "published", "disabled"]}),
    ] = None

    @field_validator("title", "body", "visibility", "status", mode="before")
    @classmethod
    def reject_null(cls, value):
        if value is None:
            raise ValueError("Explicit null is not supported")
        return value

    @field_validator("title")
    @classmethod
    def valid_title(cls, value):
        if value is not None and not 1 <= len(value) <= 200:
            raise ValueError("Invalid title")
        return value

    @field_validator("body")
    @classmethod
    def valid_body(cls, value):
        if value is not None and not 1 <= len(value) <= 100000:
            raise ValueError("Invalid body")
        return value


def _id_cursor(cursor):
    try:
        return UUID(cursor) if cursor else UUID(int=0)
    except ValueError as exc:
        raise HTTPException(422, "Invalid cursor") from exc


def _article(row):
    if row is None:
        raise HTTPException(404, "Article unavailable")
    return Article.model_validate({key: row[key] for key in Article.model_fields})


@router.get("/sections", operation_id="listSections", response_model=SectionPage)
def list_sections(
    ctx: Annotated[TenantContext, Depends(tenant_context)],
    db: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: Annotated[str | None, WithJsonSchema({"type": "string"})] = None,
):
    rows = KnowledgeRepository().list_sections(db, ctx.tenant_id, _id_cursor(cursor), limit + 1)
    return SectionPage(
        items=[Section(**row) for row in rows[:limit]],
        next_cursor=str(rows[limit - 1]["id"]) if len(rows) > limit else None,
    )


@router.post(
    "/sections",
    operation_id="createSection",
    status_code=201,
    response_model=Section,
    dependencies=[Depends(require_csrf)],
)
def create_section(
    body: CategoryInput,
    ctx: Annotated[TenantContext, Depends(admin)],
    db: Annotated[Session, Depends(get_session)],
):
    row = KnowledgeRepository().create_section(db, ctx.tenant_id, body.name)
    if row is None:
        raise HTTPException(409, "Section already exists")
    db.commit()
    return Section(**row)


@router.get("/articles", operation_id="listArticles", response_model=ArticlePage)
def list_articles(
    ctx: Annotated[TenantContext, Depends(tenant_context)],
    db: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: Annotated[str | None, WithJsonSchema({"type": "string"})] = None,
    q: Annotated[str | None, WithJsonSchema({"type": "string"})] = None,
):
    rows = KnowledgeRepository().list_articles(db, ctx.tenant_id, _id_cursor(cursor), limit + 1, q)
    return ArticlePage(
        items=[_article(row) for row in rows[:limit]],
        next_cursor=str(rows[limit - 1]["id"]) if len(rows) > limit else None,
    )


@router.post(
    "/articles",
    operation_id="createArticle",
    status_code=201,
    response_model=Article,
    dependencies=[Depends(require_csrf)],
)
def create_article(
    body: ArticleInput,
    ctx: Annotated[TenantContext, Depends(admin)],
    db: Annotated[Session, Depends(get_session)],
):
    repo = KnowledgeRepository()
    if "\x00" in body.title or "\x00" in body.body:
        raise HTTPException(422, "Invalid article text")
    if not repo.section_exists(db, ctx.tenant_id, body.section_id):
        raise HTTPException(404, "Section unavailable")
    row = repo.create_article(db, ctx.tenant_id, ctx.user_id, body)
    db.commit()
    return _article(row)


@router.get("/articles/{article_id}", operation_id="getArticle", response_model=Article)
def get_article(
    article_id: UUID,
    ctx: Annotated[TenantContext, Depends(tenant_context)],
    db: Annotated[Session, Depends(get_session)],
):
    return _article(KnowledgeRepository().get_article(db, ctx.tenant_id, article_id))


@router.patch(
    "/articles/{article_id}",
    operation_id="updateArticle",
    response_model=Article,
    dependencies=[Depends(require_csrf)],
)
def update_article(
    article_id: UUID,
    body: ArticleUpdate,
    ctx: Annotated[TenantContext, Depends(admin)],
    db: Annotated[Session, Depends(get_session)],
):
    changes = body.model_dump(exclude_unset=True, exclude={"expected_revision"})
    if any("\x00" in changes.get(key, "") for key in ("title", "body")):
        raise HTTPException(422, "Invalid article text")
    row = KnowledgeService(KnowledgeRepository()).update(
        db, ctx.tenant_id, ctx.user_id, article_id, body.expected_revision, changes
    )
    db.commit()
    return _article(row)


@router.get(
    "/articles/{article_id}/revisions",
    operation_id="listArticleRevisions",
    response_model=ArticleRevisionPage,
)
def list_revisions(
    article_id: UUID,
    ctx: Annotated[TenantContext, Depends(tenant_context)],
    db: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: Annotated[str | None, WithJsonSchema({"type": "string"})] = None,
):
    repo = KnowledgeRepository()
    if repo.get_article(db, ctx.tenant_id, article_id) is None:
        raise HTTPException(404, "Article unavailable")
    try:
        after = int(cursor) if cursor else 0
        if after < 0 or (cursor and str(after) != cursor):
            raise ValueError
    except ValueError as exc:
        raise HTTPException(422, "Invalid cursor") from exc
    rows = repo.list_revisions(db, ctx.tenant_id, article_id, after, limit + 1)
    return ArticleRevisionPage(
        items=[ArticleRevision(**row) for row in rows[:limit]],
        next_cursor=str(rows[limit - 1]["revision"]) if len(rows) > limit else None,
    )


@router.post(
    "/articles/{article_id}/restore/{revision_id}",
    operation_id="restoreArticle",
    response_model=Article,
    dependencies=[Depends(require_csrf)],
)
def restore_article(
    article_id: UUID,
    revision_id: Annotated[int, Path(ge=1)],
    ctx: Annotated[TenantContext, Depends(admin)],
    db: Annotated[Session, Depends(get_session)],
):
    row = KnowledgeService(KnowledgeRepository()).restore(
        db, ctx.tenant_id, ctx.user_id, article_id, revision_id
    )
    db.commit()
    return _article(row)


_IMPORT_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "section_id": {"type": "string", "format": "uuid"},
                        "visibility": {"type": "string", "enum": ["public", "internal"]},
                    },
                    "required": ["file", "section_id", "visibility"],
                }
            }
        },
    }
}


async def parse_import(request: Request):
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data;"):
        raise HTTPException(422, "Expected multipart form")

    # Bound the complete request before multipart can spool an unbounded file to disk.
    async def bounded_stream():
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_DOCUMENT_BYTES + 65536:
                raise MultiPartException("Import too large")
            yield chunk

    try:
        parser = MultiPartParser(
            request.headers, bounded_stream(), max_files=1, max_fields=2, max_part_size=65536
        )
        form = await parser.parse()
    except AssertionError as exc:
        raise HTTPException(503, "Multipart parser unavailable") from exc
    except MultiPartException as exc:
        raise HTTPException(422, "Invalid multipart form") from exc
    try:
        if len(form.multi_items()) != 3 or set(form.keys()) != {"file", "section_id", "visibility"}:
            raise HTTPException(422, "Invalid import fields")
        file = form.get("file")
        section_value = form.get("section_id")
        if not isinstance(file, UploadFile) or not isinstance(section_value, str):
            raise HTTPException(422, "Invalid import fields")
        try:
            section_id = UUID(section_value)
        except ValueError as exc:
            raise HTTPException(422, "Invalid section") from exc
        visibility = form.get("visibility")
        if visibility not in ("public", "internal"):
            raise HTTPException(422, "Invalid visibility")
        filename = PurePosixPath((file.filename or "").replace("\\", "/")).name
        content = await file.read(MAX_DOCUMENT_BYTES + 1)
        if filename.lower().endswith(".txt") and len(content) > MAX_TXT_BYTES + 65536:
            raise HTTPException(422, "Invalid multipart form")
        return section_id, visibility, filename, content
    finally:
        await form.close()


@router.post(
    "/imports",
    operation_id="createKnowledgeImport",
    status_code=202,
    response_model=ImportResult,
    dependencies=[Depends(require_csrf), Depends(admin)],
    openapi_extra=_IMPORT_BODY,
)
def create_import(
    ctx: Annotated[TenantContext, Depends(admin)],
    db: Annotated[Session, Depends(get_session)],
    data: Annotated[tuple, Depends(parse_import)],
):
    section_id, visibility, filename, raw = data
    repo = KnowledgeRepository()
    if not repo.section_exists(db, ctx.tenant_id, section_id):
        raise HTTPException(404, "Section unavailable")
    import_id = uuid4()
    try:
        if "\x00" in filename:
            raise ImportFailure("invalid_format")
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if extension == "txt":
            content = read_text(raw)
            import_visibility = visibility
        elif extension in ("pdf", "docx"):
            if not raw or len(raw) > MAX_DOCUMENT_BYTES:
                raise ImportFailure("too_large" if raw else "invalid_document")
            row = repo.save_import(db, ctx.tenant_id, section_id, import_id, "queued", None)
            db.execute(
                sql_text("""INSERT INTO knowledge_import_files
                (tenant_id,import_id,actor_id,filename,format,content)
                VALUES (:tenant,:id,:actor,:filename,:format,:content)"""),
                {
                    "tenant": ctx.tenant_id,
                    "id": import_id,
                    "actor": ctx.user_id,
                    "filename": filename,
                    "format": extension,
                    "content": raw,
                },
            )
            enqueue(
                db.connection(),
                ctx.tenant_id,
                "document_import",
                {"tenant_id": str(ctx.tenant_id), "import_id": str(import_id)},
                f"document_import:{import_id}",
            )
            result = ImportResult(**row)
            db.commit()
            return result
        else:
            raise ImportFailure("invalid_format")
        organization = (
            db.execute(
                sql_text("SELECT settings FROM organizations WHERE id=:tenant FOR UPDATE"),
                {"tenant": ctx.tenant_id},
            )
            .mappings()
            .one()
        )
        max_bytes = organization["settings"].get("max_knowledge_bytes", 10 * 1024 * 1024)
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
            raise ImportFailure("knowledge_limit_unavailable")
        used = db.execute(
            sql_text(
                "SELECT COALESCE(sum(octet_length(body)),0) FROM article_revisions WHERE tenant_id=:tenant"
            ),
            {"tenant": ctx.tenant_id},
        ).scalar_one()
        if used + len(content.encode("utf-8")) > max_bytes:
            raise ImportFailure("knowledge_quota_exceeded")
        title = filename.rsplit(".", 1)[0][:180] or "Imported document"
        parts = [content[index : index + 100000] for index in range(0, len(content), 100000)]
        articles = []
        for index, part in enumerate(parts):
            article = ArticleInput(
                section_id=section_id,
                title=title if len(parts) == 1 else f"{title} ({index + 1}/{len(parts)})",
                body=part,
                visibility=import_visibility,
                status="draft",
            )
            result = repo.create_article(
                db, ctx.tenant_id, ctx.user_id, article, source_type=extension, source_id=import_id
            )
            articles.append(result["id"])
        row = repo.save_import(db, ctx.tenant_id, section_id, import_id, "ready", None, articles[0])
        for index, article_id in enumerate(articles):
            db.execute(
                sql_text("""INSERT INTO knowledge_import_articles (tenant_id,import_id,article_id,position)
                VALUES (:tenant,:import,:article,:position)"""),
                {
                    "tenant": ctx.tenant_id,
                    "import": import_id,
                    "article": article_id,
                    "position": index,
                },
            )
    except ImportFailure as exc:
        row = repo.save_import(db, ctx.tenant_id, section_id, import_id, "failed", exc.code)
    db.commit()
    return ImportResult(**row)


@router.get("/imports/{import_id}", operation_id="getKnowledgeImport", response_model=ImportResult)
def get_import(
    import_id: UUID,
    ctx: Annotated[TenantContext, Depends(admin)],
    db: Annotated[Session, Depends(get_session)],
):
    row = KnowledgeRepository().get_import(db, ctx.tenant_id, import_id)
    if row is None:
        raise HTTPException(404, "Import unavailable")
    return ImportResult(**row)
