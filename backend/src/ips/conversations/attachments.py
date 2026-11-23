"""Content-based validation for customer image attachments."""

import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from PIL import Image, UnidentifiedImageError
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from ips.conversations.repository import ConversationRepository
from ips.core.db import get_session
from ips.core.ports import DTO, CustomerPrincipal, ImageInput
from ips.platform.schemas import ErrorResponse
from ips.platform.widget_api import customer_principal

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
errors: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 422, 429, 503)
}
router = APIRouter(prefix="/api/v1/public", tags=["C"], responses=errors)


class Attachment(DTO):
    id: UUID
    filename: str
    mime: str
    size: int = Field(ge=1)
    state: Literal["pending", "ready", "failed"]


class AttachmentFailure(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ImageInfo:
    mime: str
    width: int
    height: int


def validate_image(data: bytes) -> ImageInfo:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise AttachmentFailure("invalid_size")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                mime = _MIME.get(image.format)
                width, height = image.size
                if mime is None or width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                    raise AttachmentFailure("invalid_image")
                if getattr(image, "n_frames", 1) != 1:
                    raise AttachmentFailure("invalid_image")
                image.verify()
            with Image.open(BytesIO(data)) as image:
                image.load()
        return ImageInfo(mime, width, height)
    except AttachmentFailure:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise AttachmentFailure("invalid_image") from None


_UPLOAD_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"file": {"type": "string", "format": "binary"}},
                    "required": ["file"],
                }
            }
        },
    }
}


async def parse_image_upload(request: Request):
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data;"):
        raise HTTPException(422, "Expected multipart form")

    async def bounded_stream():
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_IMAGE_BYTES + 65536:
                raise MultiPartException("Image too large")
            yield chunk

    try:
        form = await MultiPartParser(
            request.headers, bounded_stream(), max_files=1, max_fields=0, max_part_size=65536
        ).parse()
    except (MultiPartException, AssertionError):
        raise HTTPException(422, "Invalid image upload") from None
    try:
        if len(form.multi_items()) != 1 or set(form.keys()) != {"file"}:
            raise HTTPException(422, "Invalid image upload")
        item = form.get("file")
        if not isinstance(item, UploadFile):
            raise HTTPException(422, "Invalid image upload")
        filename = PurePosixPath((item.filename or "").replace("\\", "/")).name
        if (
            not filename
            or len(filename) > 150
            or any(ord(c) < 32 or ord(c) == 127 for c in filename)
        ):
            raise HTTPException(422, "Invalid filename")
        return filename, await item.read(MAX_IMAGE_BYTES + 1)
    finally:
        await form.close()


@router.post(
    "/conversations/{conversation_id}/attachments",
    operation_id="uploadAttachment",
    status_code=201,
    response_model=Attachment,
    openapi_extra=_UPLOAD_BODY,
)
def upload_attachment(
    conversation_id: UUID,
    principal: Annotated[CustomerPrincipal, Depends(customer_principal)],
    db: Annotated[Session, Depends(get_session)],
    data: Annotated[tuple, Depends(parse_image_upload)],
):
    if ConversationRepository().find_customer(db, principal, conversation_id, lock=True) is None:
        raise HTTPException(404, "Conversation unavailable")
    filename, content = data
    try:
        image = validate_image(content)
    except AttachmentFailure:
        raise HTTPException(422, "Invalid image") from None
    row = (
        db.execute(
            text("""INSERT INTO attachments
        (id,tenant_id,conversation_id,customer_id,widget_id,filename,mime,size,state,content)
        VALUES (:id,:tenant,:conversation,:customer,:widget,:filename,:mime,:size,'ready',:content)
        RETURNING id,filename,mime,size,state"""),
            {
                "id": uuid4(),
                "tenant": principal.tenant_id,
                "conversation": conversation_id,
                "customer": principal.customer_id,
                "widget": principal.widget_id,
                "filename": filename,
                "mime": image.mime,
                "size": len(content),
                "content": content,
            },
        )
        .mappings()
        .one()
    )
    result = Attachment.model_validate(dict(row))
    db.commit()
    return result


@router.get(
    "/attachments/{attachment_id}",
    operation_id="downloadCustomerAttachment",
    response_class=Response,
    responses={
        200: {
            "description": "Authorized image or download",
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
def download_attachment(
    attachment_id: UUID,
    principal: Annotated[CustomerPrincipal, Depends(customer_principal)],
    db: Annotated[Session, Depends(get_session)],
):
    row = (
        db.execute(
            text("""SELECT a.filename,a.content FROM attachments a
        JOIN conversations c ON c.tenant_id=a.tenant_id AND c.id=a.conversation_id
        WHERE a.tenant_id=:tenant AND a.id=:id AND a.customer_id=:customer AND a.widget_id=:widget
          AND c.customer_id=:customer AND c.widget_id=:widget AND a.state='ready'
          AND (CAST(:scope AS uuid) IS NULL OR a.conversation_id=:scope)"""),
            {
                "tenant": principal.tenant_id,
                "id": attachment_id,
                "customer": principal.customer_id,
                "widget": principal.widget_id,
                "scope": principal.conversation_scope,
            },
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, "Attachment unavailable")
    return Response(
        content=bytes(row["content"]),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''"
            + quote(row["filename"], safe=""),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


def images_for_message(db, principal, conversation_id, message_id, *, vision_supported):
    """Return bytes only for an authorized message when the configured provider supports vision."""
    if not vision_supported:
        return []
    if ConversationRepository().find_customer(db, principal, conversation_id) is None:
        raise HTTPException(404, "Conversation unavailable")
    rows = (
        db.execute(
            text("""SELECT a.id,a.mime,a.content FROM attachments a
        JOIN message_attachments ma ON ma.tenant_id=a.tenant_id AND ma.conversation_id=a.conversation_id AND ma.attachment_id=a.id
        WHERE a.tenant_id=:tenant AND a.conversation_id=:conversation AND ma.message_id=:message
          AND a.customer_id=:customer AND a.widget_id=:widget AND a.state='ready' ORDER BY ma.position"""),
            {
                "tenant": principal.tenant_id,
                "conversation": conversation_id,
                "message": message_id,
                "customer": principal.customer_id,
                "widget": principal.widget_id,
            },
        )
        .mappings()
        .all()
    )
    return [
        ImageInput(attachment_id=row["id"], mime=row["mime"], data=bytes(row["content"]))
        for row in rows
    ]
