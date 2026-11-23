"""C09 acceptance tests: bounded documents, images and private vector search."""

from io import BytesIO

import pytest
from docx import Document
from PIL import Image
from pypdf import PdfWriter

from ips.conversations.attachments import AttachmentFailure, validate_image
from ips.knowledge.parsers import DocumentFailure, parse_document


def test_pdf_without_text_is_marked_needs_ocr():
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    stream = BytesIO()
    writer.write(stream)
    parsed = parse_document(stream.getvalue(), "pdf")
    assert parsed.state == "needs_ocr" and parsed.text == ""


def test_docx_extracts_text_and_preserves_private_content_for_review():
    document = Document()
    document.add_paragraph("Synthetic contact alice@example.invalid")
    stream = BytesIO()
    document.save(stream)
    parsed = parse_document(stream.getvalue(), "docx")
    assert parsed.state == "ready" and "alice@example.invalid" in parsed.text


def test_more_than_100_pdf_pages_and_docx_explicit_pages_are_rejected():
    writer = PdfWriter()
    for _ in range(101):
        writer.add_blank_page(width=100, height=100)
    pdf = BytesIO()
    writer.write(pdf)
    with pytest.raises(DocumentFailure) as error:
        parse_document(pdf.getvalue(), "pdf")
    assert error.value.code == "too_many_pages"
    document = Document()
    for _ in range(100):
        document.add_page_break()
    docx = BytesIO()
    document.save(docx)
    with pytest.raises(DocumentFailure) as error:
        parse_document(docx.getvalue(), "docx")
    assert error.value.code == "too_many_pages"


@pytest.mark.parametrize("kind,data", [("pdf", b"%PDF-1.7 corrupt"), ("docx", b"not a zip")])
def test_malformed_documents_are_rejected(kind, data):
    with pytest.raises(DocumentFailure):
        parse_document(data, kind)


@pytest.mark.parametrize("kind", ["pdf", "docx"])
def test_oversized_document_rejected_before_parsing(kind):
    with pytest.raises(DocumentFailure) as error:
        parse_document(b"x" * (10 * 1024 * 1024 + 1), kind)
    assert error.value.code == "too_large"


@pytest.mark.parametrize(
    "format_name,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")]
)
def test_images_are_validated_by_content(format_name, mime):
    stream = BytesIO()
    Image.new("RGB", (3, 2), "red").save(stream, format=format_name)
    result = validate_image(stream.getvalue())
    assert (result.mime, result.width, result.height) == (mime, 3, 2)


def test_malformed_and_oversized_images_are_rejected():
    with pytest.raises(AttachmentFailure):
        validate_image(b"not an image")
    with pytest.raises(AttachmentFailure):
        validate_image(b"x" * (5 * 1024 * 1024 + 1))
    stream = BytesIO()
    Image.new("1", (5001, 4000)).save(stream, format="PNG")
    with pytest.raises(AttachmentFailure):
        validate_image(stream.getvalue())


def test_vision_images_require_explicit_capability_and_use_stub_transport():
    import json
    from uuid import uuid4

    from ips.ai.fake import FakeLLMProvider
    from ips.ai.http_provider import OpenAIProvider
    from ips.ai.provider import AnswerInput, ImageInput, ProviderFailure

    stream = BytesIO()
    Image.new("RGB", (2, 2), "blue").save(stream, format="PNG")
    article_id = uuid4()
    data = AnswerInput(
        text="refund",
        history=[],
        snippets=[
            {
                "article_id": str(article_id),
                "revision": 1,
                "title": "Public",
                "text": "refund",
                "visibility": "public",
            }
        ],
        images=[ImageInput(attachment_id=uuid4(), mime="image/png", data=stream.getvalue())],
    )
    called = []

    def transport(url, headers, payload, timeout):
        called.append(payload)
        answer = {
            "text": "answer",
            "citations": [{"article_id": str(article_id), "revision": 1, "title": "Public"}],
            "needs_human": False,
            "category": None,
            "priority": "normal",
            "reason": None,
        }
        return {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(answer)}],
                }
            ],
        }

    with pytest.raises(ProviderFailure):
        FakeLLMProvider().answer(data, timeout_seconds=20)
    disabled = OpenAIProvider(api_key="synthetic", model="test", transport=transport)
    with pytest.raises(ProviderFailure):
        disabled.answer(data, timeout_seconds=20)
    assert not called
    enabled = OpenAIProvider(
        api_key="synthetic", model="test", transport=transport, vision_supported=True
    )
    assert enabled.answer(data, timeout_seconds=20).text == "answer"
    parts = called[0]["input"][0]["content"]
    assert parts[0]["type"] == "input_text" and parts[1]["type"] == "input_image"
    assert parts[1]["image_url"].startswith("data:image/png;base64,")


@pytest.fixture
def tenant_app():
    from uuid import uuid4

    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from ips.conversations.repository import ConversationRepository
    from ips.core.config import Settings
    from ips.core.ports import CustomerPrincipal, TenantContext
    from ips.main import create_app
    from ips.platform.auth import require_csrf, tenant_context
    from ips.platform.widget_api import customer_principal

    app = create_app(Settings())
    tenant, widget, customer, section = (uuid4() for _ in range(4))
    principal = CustomerPrincipal(tenant_id=tenant, widget_id=widget, customer_id=customer)
    ctx = TenantContext(tenant_id=tenant, user_id=uuid4(), membership_id=uuid4(), role="admin")
    app.dependency_overrides[customer_principal] = lambda: principal
    app.dependency_overrides[tenant_context] = lambda: ctx
    app.dependency_overrides[require_csrf] = lambda: None
    with Session(app.state.engine) as db, db.begin():
        db.execute(text("INSERT INTO organizations (id,name) VALUES (:id,'c09')"), {"id": tenant})
        db.execute(
            text("""INSERT INTO widgets (id,tenant_id,name,public_key,color,greeting,allowed_origins)
          VALUES (:id,:tenant,'test',:key,'#ffffff','hello',ARRAY['https://example.test'])"""),
            {"id": widget, "tenant": tenant, "key": str(widget)},
        )
        db.execute(
            text("INSERT INTO customers (id,tenant_id) VALUES (:id,:tenant)"),
            {"id": customer, "tenant": tenant},
        )
        db.execute(
            text("INSERT INTO article_sections (id,tenant_id,name) VALUES (:id,:tenant,'section')"),
            {"id": section, "tenant": tenant},
        )
        db.execute(
            text(
                "INSERT INTO widget_sections (tenant_id,widget_id,section_id) VALUES (:tenant,:widget,:section)"
            ),
            {"tenant": tenant, "widget": widget, "section": section},
        )
        conversation = ConversationRepository().create(db, principal)["id"]
    with TestClient(app) as client:
        yield app, client, principal, ctx, section, conversation
    with Session(app.state.engine) as db, db.begin():
        params = {"tenant": tenant}
        for table in (
            "knowledge_import_articles",
            "knowledge_chunks",
            "knowledge_import_files",
            "knowledge_imports",
            "jobs",
            "tickets",
            "ai_runs",
            "message_attachments",
            "messages",
            "attachments",
            "conversations",
            "widget_sections",
            "customers",
            "widgets",
        ):
            db.execute(text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), params)
        db.execute(
            text("UPDATE articles SET current_revision_id=NULL WHERE tenant_id=:tenant"), params
        )
        for table in ("article_revisions", "articles", "article_sections"):
            db.execute(text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), params)
        db.execute(text("DELETE FROM organizations WHERE id=:tenant"), params)
    app.state.engine.dispose()


def test_attachment_upload_download_and_message_isolation(tenant_app):
    from uuid import uuid4

    from sqlalchemy.orm import Session

    from ips.conversations.attachments import images_for_message
    from ips.platform.widget_api import customer_principal

    app, client, principal, _, _, conversation = tenant_app
    image = BytesIO()
    Image.new("RGB", (2, 2), "green").save(image, format="PNG")
    payload = image.getvalue()
    result = client.post(
        f"/api/v1/public/conversations/{conversation}/attachments",
        files={"file": ("picture.png", payload, "image/png")},
    )
    assert result.status_code == 201, result.text
    attachment_id = result.json()["id"]
    path = f"/api/v1/public/attachments/{attachment_id}"
    assert client.get(path).content == payload
    key = str(uuid4())
    message = {"client_message_id": key, "text": "photo", "attachment_ids": [attachment_id]}
    message_path = f"/api/v1/public/conversations/{conversation}/messages"
    sent = client.post(message_path, json=message)
    assert sent.status_code == 201 and sent.json()["attachment_ids"] == [attachment_id]
    assert client.post(message_path, json=message).status_code == 200
    assert client.post(message_path, json={**message, "attachment_ids": []}).status_code == 409
    with Session(app.state.engine) as db:
        from uuid import UUID

        mid = UUID(sent.json()["id"])
        assert images_for_message(db, principal, conversation, mid, vision_supported=False) == []
        images = images_for_message(db, principal, conversation, mid, vision_supported=True)
        assert len(images) == 1 and images[0].data == payload
    from ips.conversations.repository import ConversationRepository

    with Session(app.state.engine) as db, db.begin():
        another_conversation = ConversationRepository().create(db, principal)["id"]
    other_path = f"/api/v1/public/conversations/{another_conversation}/messages"
    assert (
        client.post(other_path, json={**message, "client_message_id": str(uuid4())}).status_code
        == 404
    )
    assert (
        client.post(
            message_path,
            json={
                **message,
                "client_message_id": str(uuid4()),
                "attachment_ids": [attachment_id, attachment_id],
            },
        ).status_code
        == 422
    )
    for changes in (
        {"tenant_id": uuid4()},
        {"customer_id": uuid4()},
        {"widget_id": uuid4()},
        {"conversation_scope": uuid4()},
    ):
        app.dependency_overrides[customer_principal] = lambda c=changes: principal.model_copy(
            update=c
        )
        assert client.get(path).status_code == 404


def test_pdf_docx_imports_are_private_drafts_and_respect_quota(tenant_app):
    from time import monotonic, sleep

    from sqlalchemy import text
    from sqlalchemy.orm import Session

    app, client, principal, _, section, _ = tenant_app

    def completed(response):
        assert response.status_code == 202, response.text
        assert response.json()["state"] == "queued"
        path = "/api/v1/knowledge/imports/" + response.json()["id"]
        deadline = monotonic() + 15
        while monotonic() < deadline:
            item = client.get(path).json()
            if item["state"] in ("ready", "failed"):
                return item
            sleep(0.05)
        pytest.fail("Document worker did not finish")

    document = Document()
    document.add_paragraph("Synthetic PII alice@example.invalid")
    stream = BytesIO()
    document.save(stream)
    response = client.post(
        "/api/v1/knowledge/imports",
        data={"section_id": str(section), "visibility": "public"},
        files={"file": ("sample.docx", stream.getvalue(), "application/octet-stream")},
    )
    assert completed(response)["state"] == "ready"
    with Session(app.state.engine) as db:
        row = db.execute(
            text("""SELECT a.visibility,a.status,r.body FROM articles a JOIN article_revisions r
            ON r.id=a.current_revision_id WHERE a.tenant_id=:tenant"""),
            {"tenant": principal.tenant_id},
        ).one()
        assert (row.visibility, row.status) == ("internal", "draft")
        assert "alice@example.invalid" in row.body
    with app.state.engine.begin() as connection:
        connection.execute(
            text("UPDATE organizations SET settings=CAST(:settings AS jsonb) WHERE id=:id"),
            {"settings": '{"max_knowledge_bytes":1}', "id": principal.tenant_id},
        )
    blocked = client.post(
        "/api/v1/knowledge/imports",
        data={"section_id": str(section), "visibility": "public"},
        files={"file": ("more.docx", stream.getvalue(), "application/octet-stream")},
    )
    assert completed(blocked)["error_code"] == "knowledge_quota_exceeded"
    blank = PdfWriter()
    blank.add_blank_page(width=200, height=200)
    pdf = BytesIO()
    blank.write(pdf)
    scanned = client.post(
        "/api/v1/knowledge/imports",
        data={"section_id": str(section), "visibility": "public"},
        files={"file": ("scan.pdf", pdf.getvalue(), "application/pdf")},
    )
    assert completed(scanned)["error_code"] == "needs_ocr"


def test_semantic_search_never_returns_closer_internal_chunk(tenant_app):
    from sqlalchemy.orm import Session, sessionmaker

    from ips.knowledge.embeddings import index_article
    from ips.knowledge.repository import KnowledgeRepository
    from ips.knowledge.retrieval import PostgresKnowledgeSearch
    from ips.knowledge.schemas import ArticleInput

    app, _, principal, ctx, section, _ = tenant_app

    class Provider:
        model = "test-model"
        dimension = 2
        local_only = True

        def embed(self, text):
            return [1.0, 0.0] if "secret" in text else [0.8, 0.6]

    with Session(app.state.engine) as db, db.begin():
        repo = KnowledgeRepository()
        public = repo.create_article(
            db,
            principal.tenant_id,
            ctx.user_id,
            ArticleInput(
                section_id=section,
                title="Public",
                body="public text",
                visibility="public",
                status="published",
            ),
        )
        private = repo.create_article(
            db,
            principal.tenant_id,
            ctx.user_id,
            ArticleInput(
                section_id=section,
                title="Private",
                body="secret text",
                visibility="internal",
                status="published",
            ),
        )
        public_id, private_id = public["id"], private["id"]
    sessions = sessionmaker(app.state.engine)
    assert index_article(sessions, principal.tenant_id, public_id, Provider()) == 1
    from ips.knowledge.embeddings import EmbeddingFailure

    with pytest.raises(EmbeddingFailure):
        index_article(sessions, principal.tenant_id, private_id, Provider())
    assert (
        index_article(sessions, principal.tenant_id, private_id, Provider(), allow_internal=True)
        == 1
    )
    search = PostgresKnowledgeSearch(sessions)
    found = search.semantic_search(
        principal.tenant_id, principal.widget_id, [1, 0], model="test-model", dimension=2
    )
    assert [item.article_id for item in found] == [public_id]
    assert (
        search.semantic_search(
            principal.tenant_id, principal.widget_id, [1, 0], model="different-model", dimension=2
        )
        == []
    )
    configured = PostgresKnowledgeSearch(sessions, embedding_provider=Provider())
    assert [
        item.article_id
        for item in configured.search(
            principal.tenant_id, principal.widget_id, "secret", "customer"
        )
    ] == [public_id]
