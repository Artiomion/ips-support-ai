"""Explicit embedding contracts and an offline deterministic development provider."""

import hashlib
import math
import re
from uuid import uuid4

from sqlalchemy import text


class EmbeddingFailure(ValueError):
    pass


def vector_literal(values, dimension):
    if not 1 <= dimension <= 2000 or len(values) != dimension:
        raise EmbeddingFailure("invalid_dimension")
    vector = [float(value) for value in values]
    if not all(math.isfinite(value) for value in vector) or not any(vector):
        raise EmbeddingFailure("invalid_vector")
    return "[" + ",".join(format(value, ".9g") for value in vector) + "]"


class FakeEmbeddingProvider:
    """Non-semantic, deterministic hashed words for offline demonstrations only."""

    model = "demo-hash-v1"
    local_only = True

    def __init__(self, dimension=64):
        if not 1 <= dimension <= 2000:
            raise EmbeddingFailure("invalid_dimension")
        self.dimension = dimension

    def embed(self, value):
        vector = [0.0] * self.dimension
        for word in re.findall(r"\w+", value.casefold()):
            index = (
                int.from_bytes(hashlib.blake2b(word.encode("utf-8"), digest_size=8).digest(), "big")
                % self.dimension
            )
            vector[index] += 1.0
        if not any(vector):
            raise EmbeddingFailure("empty_input")
        return vector


def index_article(sessions, tenant_id, article_id, provider, *, allow_internal=False):
    """Embed outside a DB transaction; publish chunks only if the revision is unchanged."""
    model = provider.model
    dimension = provider.dimension
    if not isinstance(model, str) or not model.strip() or len(model) > 100:
        raise EmbeddingFailure("invalid_model")
    with sessions() as db:
        row = (
            db.execute(
                text("""SELECT a.id,a.visibility,r.id AS revision_id,r.body FROM articles a
            JOIN article_revisions r ON r.tenant_id=a.tenant_id AND r.article_id=a.id AND r.id=a.current_revision_id
            WHERE a.tenant_id=:tenant AND a.id=:article"""),
                {"tenant": tenant_id, "article": article_id},
            )
            .mappings()
            .first()
        )
        if row is None:
            raise EmbeddingFailure("article_unavailable")
        if row["visibility"] != "public" and not (
            allow_internal and getattr(provider, "local_only", False) is True
        ):
            raise EmbeddingFailure("internal_embedding_disabled")
        revision_id, body = row["revision_id"], row["body"]
    # Each chunk preserves exact stored text and stays within the database bound.
    chunks = [body[i : i + 2000] for i in range(0, len(body), 2000)]
    encoded = [(chunk, vector_literal(provider.embed(chunk), dimension)) for chunk in chunks]
    with sessions.begin() as db:
        current = db.execute(
            text("""SELECT current_revision_id FROM articles
            WHERE tenant_id=:tenant AND id=:article FOR UPDATE"""),
            {"tenant": tenant_id, "article": article_id},
        ).scalar_one_or_none()
        if current != revision_id:
            raise EmbeddingFailure("revision_changed")
        db.execute(
            text("""DELETE FROM knowledge_chunks WHERE tenant_id=:tenant AND article_id=:article
            AND revision_id=:revision AND embedding_model=:model"""),
            {"tenant": tenant_id, "article": article_id, "revision": revision_id, "model": model},
        )
        for position, (chunk, vector) in enumerate(encoded):
            db.execute(
                text("""INSERT INTO knowledge_chunks
                (id,tenant_id,article_id,revision_id,position,content,embedding,embedding_model,embedding_dimension)
                VALUES (:id,:tenant,:article,:revision,:position,:content,CAST(:vector AS vector),:model,:dimension)"""),
                {
                    "id": uuid4(),
                    "tenant": tenant_id,
                    "article": article_id,
                    "revision": revision_id,
                    "position": position,
                    "content": chunk,
                    "vector": vector,
                    "model": model,
                    "dimension": dimension,
                },
            )
    return len(encoded)
