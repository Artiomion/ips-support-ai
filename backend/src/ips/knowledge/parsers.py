"""Bounded PDF and DOCX extraction; untrusted documents run in a child process."""

import logging
import multiprocessing
from dataclasses import dataclass
from io import BytesIO
from threading import Event, Thread
from uuid import UUID
from zipfile import ZipFile

from sqlalchemy import text
from sqlalchemy.orm import Session

from ips.core.jobs import drain_once
from ips.knowledge.repository import KnowledgeRepository
from ips.knowledge.schemas import ArticleInput

logger = logging.getLogger(__name__)
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_DOCUMENT_CHARS = 1_000_000
MAX_PDF_PAGES = 100
MAX_DOCX_UNCOMPRESSED = 64 * 1024 * 1024


class DocumentFailure(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ParsedDocument:
    state: str
    text: str


def _extract(data, kind):
    if kind == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise DocumentFailure("encrypted_document")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise DocumentFailure("too_many_pages")
        parts = []
        size = 0
        for page in reader.pages:
            value = page.extract_text() or ""
            size += len(value)
            if size > MAX_DOCUMENT_CHARS:
                raise DocumentFailure("too_many_characters")
            parts.append(value)
        value = "\n".join(parts).strip()
        if len(value) > MAX_DOCUMENT_CHARS:
            raise DocumentFailure("too_many_characters")
        return ParsedDocument("ready" if value else "needs_ocr", value)
    if kind == "docx":
        from docx import Document

        with ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            if (
                len(entries) > 5000
                or sum(item.file_size for item in entries) > MAX_DOCX_UNCOMPRESSED
            ):
                raise DocumentFailure("expanded_document_too_large")
            if any(item.flag_bits & 1 for item in entries):
                raise DocumentFailure("encrypted_document")
            if any(
                item.file_size > 1024 * 1024 and item.file_size > max(item.compress_size, 1) * 200
                for item in entries
            ):
                raise DocumentFailure("expanded_document_too_large")
        doc = Document(BytesIO(data))
        # Word has no reliable page count without a layout renderer. Bound explicit
        # page breaks and cached page-break markers; implicit pagination is not verified.
        breaks = doc.element.xpath(
            './/w:br[@w:type="page"] | .//w:lastRenderedPageBreak | .//w:pageBreakBefore'
        )
        if len(breaks) >= MAX_PDF_PAGES:
            raise DocumentFailure("too_many_pages")
        parts = []
        count = 0
        for paragraph in doc.paragraphs:
            count += len(paragraph.text) + 1
            if count > MAX_DOCUMENT_CHARS:
                raise DocumentFailure("too_many_characters")
            parts.append(paragraph.text)
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    count += len(cell.text) + 1
                    if count > MAX_DOCUMENT_CHARS:
                        raise DocumentFailure("too_many_characters")
                    parts.append(cell.text)
        value = "\n".join(parts).strip()
        if not value:
            raise DocumentFailure("empty_document")
        return ParsedDocument("ready", value)
    raise DocumentFailure("invalid_format")


def _child(connection, data, kind):
    try:
        result = _extract(data, kind)
        connection.send(("ok", result.state, result.text))
    except DocumentFailure as error:
        connection.send(("error", error.code))
    except Exception:  # noqa: BLE001 - redact document content and parser internals.
        connection.send(("error", "invalid_document"))
    finally:
        connection.close()


def parse_document(data: bytes, kind: str, *, timeout_seconds: float = 20) -> ParsedDocument:
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentFailure("too_large")
    if not data or kind not in ("pdf", "docx"):
        raise DocumentFailure("invalid_document")
    if timeout_seconds <= 0 or timeout_seconds > 20:
        raise DocumentFailure("invalid_timeout")
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_child, args=(child, data, kind), daemon=True)
    try:
        process.start()
        child.close()
        if not parent.poll(timeout_seconds):
            raise DocumentFailure("timeout")
        try:
            result = parent.recv()
        except (EOFError, OSError):
            raise DocumentFailure("invalid_document") from None
        if result[0] != "ok":
            raise DocumentFailure(result[1])
        return ParsedDocument(result[1], result[2])
    finally:
        parent.close()
        if process.is_alive():
            process.terminate()
        if process.pid is not None:
            process.join(timeout=2)


def process_document_import(engine, payload):
    """Idempotent B01/B09 handler: extract outside DB transactions and save private drafts."""
    tenant_id, import_id = UUID(payload["tenant_id"]), UUID(payload["import_id"])
    params = {"tenant": tenant_id, "id": import_id}
    with Session(engine) as db:
        row = (
            db.execute(
                text("""SELECT f.actor_id,f.filename,f.format,f.content,i.section_id
            FROM knowledge_import_files f JOIN knowledge_imports i
              ON i.tenant_id=f.tenant_id AND i.id=f.import_id
            WHERE f.tenant_id=:tenant AND f.import_id=:id AND i.state='queued'"""),
                params,
            )
            .mappings()
            .first()
        )
        if row is None:
            return
        source = dict(row)
    failure = None
    try:
        parsed = parse_document(bytes(source["content"]), source["format"])
        if parsed.state == "needs_ocr":
            raise DocumentFailure("needs_ocr")
    except DocumentFailure as error:
        failure = error.code
    with Session(engine) as db, db.begin():
        settings = db.execute(
            text("SELECT settings FROM organizations WHERE id=:tenant FOR UPDATE"), params
        ).scalar_one_or_none()
        current = db.execute(
            text(
                "SELECT state FROM knowledge_imports WHERE tenant_id=:tenant AND id=:id FOR UPDATE"
            ),
            params,
        ).scalar_one_or_none()
        if current != "queued":
            return
        if settings is None:
            raise DocumentFailure("organization_unavailable")
        if failure is None:
            maximum = settings.get("max_knowledge_bytes", 10 * 1024 * 1024)
            if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
                failure = "knowledge_limit_unavailable"
            else:
                used = db.execute(
                    text(
                        "SELECT COALESCE(sum(octet_length(body)),0) FROM article_revisions WHERE tenant_id=:tenant"
                    ),
                    params,
                ).scalar_one()
                if used + len(parsed.text.encode("utf-8")) > maximum:
                    failure = "knowledge_quota_exceeded"
        if failure is None:
            content = parsed.text
            title = source["filename"].rsplit(".", 1)[0][:180] or "Imported document"
            parts = [content[i : i + 100000] for i in range(0, len(content), 100000)]
            repo = KnowledgeRepository()
            article_ids = []
            for index, part in enumerate(parts):
                article = ArticleInput(
                    section_id=source["section_id"],
                    title=title if len(parts) == 1 else f"{title} ({index + 1}/{len(parts)})",
                    body=part,
                    visibility="internal",
                    status="draft",
                )
                result = repo.create_article(
                    db,
                    tenant_id,
                    source["actor_id"],
                    article,
                    source_type=source["format"],
                    source_id=import_id,
                )
                article_ids.append(result["id"])
            for index, article_id in enumerate(article_ids):
                db.execute(
                    text("""INSERT INTO knowledge_import_articles (tenant_id,import_id,article_id,position)
                    VALUES (:tenant,:id,:article,:position)"""),
                    {**params, "article": article_id, "position": index},
                )
            db.execute(
                text(
                    "UPDATE knowledge_imports SET state='ready',article_id=:article WHERE tenant_id=:tenant AND id=:id"
                ),
                {**params, "article": article_ids[0]},
            )
        else:
            db.execute(
                text(
                    "UPDATE knowledge_imports SET state='failed',error_code=:error WHERE tenant_id=:tenant AND id=:id"
                ),
                {**params, "error": failure},
            )
        db.execute(
            text("DELETE FROM knowledge_import_files WHERE tenant_id=:tenant AND import_id=:id"),
            params,
        )


class DocumentImportRunner:
    """Small interim API worker; B09 can register the same handler in its worker."""

    def __init__(self, engine, poll_seconds=1.0):
        self.engine, self.poll_seconds = engine, poll_seconds
        self.stop_event = Event()
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = Thread(target=self._run, name="document-import", daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop_event.is_set():
            try:
                worked = drain_once(
                    self.engine,
                    {
                        "document_import": lambda payload: process_document_import(
                            self.engine, payload
                        )
                    },
                )
            except Exception:  # noqa: BLE001 - do not log file contents or private database details.
                logger.warning("Document import worker failed")
                worked = False
            if not worked:
                self.stop_event.wait(self.poll_seconds)

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=25)
