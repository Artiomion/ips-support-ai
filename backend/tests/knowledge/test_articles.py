from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from ips.knowledge.imports import ImportFailure, read_text
from ips.knowledge.service import KnowledgeService


class FakeRepository:
    def __init__(self):
        self.articles = {}
        self.revisions = {}

    def lock_article(self, uow, tenant_id, article_id):
        article = self.articles.get(article_id)
        return article if article and article["tenant_id"] == tenant_id else None

    def get_revision(self, uow, tenant_id, article_id, revision):
        return self.revisions.get((tenant_id, article_id, revision))

    def save_revision(self, uow, article, changes, actor_id, source_type):
        updated = {**article, **changes, "revision": article["revision"] + 1}
        self.articles[article["id"]] = updated
        self.revisions[(article["tenant_id"], article["id"], updated["revision"])] = {
            "title": updated["title"],
            "body": updated["body"],
            "revision": updated["revision"],
        }
        return updated


def test_revision_conflict_and_restore_preserves_history():
    tenant_id, article_id, actor_id, section_id = (uuid4() for _ in range(4))
    repo = FakeRepository()
    original = {
        "id": article_id,
        "tenant_id": tenant_id,
        "section_id": section_id,
        "title": "Old",
        "body": "First",
        "visibility": "public",
        "status": "published",
        "revision": 1,
    }
    repo.articles[article_id] = original
    repo.revisions[(tenant_id, article_id, 1)] = {"title": "Old", "body": "First", "revision": 1}
    service = KnowledgeService(repo)
    updated = service.update(None, tenant_id, actor_id, article_id, 1, {"body": "Second"})
    assert updated["revision"] == 2
    with pytest.raises(HTTPException) as error:
        service.update(None, tenant_id, actor_id, article_id, 1, {"title": "Stale"})
    assert error.value.status_code == 409
    restored = service.restore(None, tenant_id, actor_id, article_id, 1)
    assert restored["revision"] == 3 and restored["body"] == "First"
    assert repo.revisions[(tenant_id, article_id, 2)]["body"] == "Second"
    with pytest.raises(HTTPException) as error:
        service.restore(None, uuid4(), actor_id, article_id, 1)
    assert error.value.status_code == 404


def test_import_rejects_invalid_utf8_and_oversized():
    assert read_text(b"Hello") == "Hello"
    for data, code in ((b"\xff", "invalid_utf8"), (b"a" * (2 * 1024 * 1024 + 1), "too_large")):
        with pytest.raises(ImportFailure) as error:
            read_text(data)
        assert error.value.code == code


def test_restoring_old_internal_content_requires_republication():
    tenant, article_id, actor_id, section_id = (uuid4() for _ in range(4))
    repo = FakeRepository()
    repo.articles[article_id] = {
        "id": article_id,
        "tenant_id": tenant,
        "section_id": section_id,
        "title": "Guide",
        "body": "Safe public content",
        "visibility": "public",
        "status": "published",
        "revision": 2,
    }
    repo.revisions[(tenant, article_id, 1)] = {
        "title": "Internal",
        "body": "Private content",
        "revision": 1,
    }
    restored = KnowledgeService(repo).restore(None, tenant, actor_id, article_id, 1)
    assert restored["revision"] == 3
    assert restored["body"] == "Private content"
    assert restored["status"] == "draft"


def test_knowledge_routes_match_openapi():
    import json
    from pathlib import Path

    from fastapi import FastAPI

    from ips.core.export_openapi import compare_contracts
    from ips.knowledge.router import router

    app = FastAPI()
    app.include_router(router)
    path = Path(__file__).resolve().parents[3] / "contracts" / "openapi.json"
    assert compare_contracts(json.loads(path.read_text(encoding="utf-8")), app.openapi()) == []


def test_postgres_revisions_privacy_and_imports():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from ips.core.config import Settings
    from ips.core.db import get_session
    from ips.core.ports import TenantContext
    from ips.knowledge.repository import KnowledgeRepository
    from ips.knowledge.router import admin, router
    from ips.platform.auth import require_csrf, tenant_context

    engine = create_engine(Settings().database_url)
    tenant, other = uuid4(), uuid4()
    ctx = TenantContext(tenant_id=tenant, user_id=uuid4(), membership_id=uuid4(), role="admin")
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            db = Session(bind=connection)
            for identifier in (tenant, other):
                db.execute(
                    text("INSERT INTO organizations (id,name) VALUES (:id,'knowledge test')"),
                    {"id": identifier},
                )
            app = FastAPI()
            app.include_router(router)
            app.dependency_overrides[get_session] = lambda: db
            app.dependency_overrides[admin] = lambda: ctx
            app.dependency_overrides[tenant_context] = lambda: ctx
            app.dependency_overrides[require_csrf] = lambda: None
            with TestClient(app) as client:
                section = client.post("/api/v1/knowledge/sections", json={"name": "Support"})
                assert section.status_code == 201, section.text
                section_id = section.json()["id"]
                payload = {
                    "section_id": section_id,
                    "title": "Refund",
                    "body": "Refund policy",
                    "visibility": "public",
                    "status": "published",
                }
                created = client.post("/api/v1/knowledge/articles", json=payload)
                assert created.status_code == 201, created.text
                article_id = created.json()["id"]
                path = f"/api/v1/knowledge/articles/{article_id}"
                assert (
                    client.patch(path, json={"expected_revision": 1, "body": "New policy"}).json()[
                        "revision"
                    ]
                    == 2
                )
                assert (
                    client.patch(path, json={"expected_revision": 1, "body": "Stale"}).status_code
                    == 409
                )
                restored = client.post(f"{path}/restore/1")
                assert restored.status_code == 200, restored.text
                assert (
                    restored.json()["revision"] == 3 and restored.json()["body"] == "Refund policy"
                )
                history = client.get(f"{path}/revisions").json()["items"]
                assert [r["body"] for r in history] == [
                    "Refund policy",
                    "New policy",
                    "Refund policy",
                ]
                repo = KnowledgeRepository()
                assert restored.json()["status"] == "draft"
                assert not repo.public_candidates(db, tenant, [UUID(section_id)], "Refund")
                published = client.patch(path, json={"expected_revision": 3, "status": "published"})
                assert published.status_code == 200
                assert len(repo.public_candidates(db, tenant, [UUID(section_id)], "Refund")) == 1
                assert not repo.public_candidates(db, other, [UUID(section_id)], "Refund")
                changed = client.patch(
                    path, json={"expected_revision": 4, "visibility": "internal"}
                )
                assert changed.status_code == 200, changed.text
                assert not repo.public_candidates(db, tenant, [UUID(section_id)], "Refund")
                changed = client.patch(
                    path,
                    json={"expected_revision": 5, "visibility": "public", "status": "disabled"},
                )
                assert changed.status_code == 200, changed.text
                assert not repo.public_candidates(db, tenant, [UUID(section_id)], "Refund")
                private = client.post(
                    "/api/v1/knowledge/articles",
                    json={
                        **payload,
                        "title": "Secret",
                        "body": "Private marker",
                        "visibility": "internal",
                    },
                )
                assert private.status_code == 201
                private_path = "/api/v1/knowledge/articles/" + private.json()["id"]
                clean = client.patch(
                    private_path,
                    json={
                        "expected_revision": 1,
                        "title": "Public",
                        "body": "Clean text",
                        "visibility": "public",
                    },
                )
                assert clean.status_code == 200
                recovered = client.post(private_path + "/restore/1")
                assert recovered.status_code == 200
                assert recovered.json()["body"] == "Private marker"
                assert recovered.json()["status"] == "draft"
                assert not repo.public_candidates(db, tenant, [UUID(section_id)], "Private marker")
                for content, expected_state, error in (
                    (b"Valid UTF-8", "ready", None),
                    (b"\xff", "failed", "invalid_utf8"),
                    (b"a" * (2 * 1024 * 1024 + 1), "failed", "too_large"),
                ):
                    response = client.post(
                        "/api/v1/knowledge/imports",
                        data={"section_id": section_id, "visibility": "public"},
                        files={"file": ("document.txt", content, "text/plain")},
                    )
                    assert response.status_code == 202, response.text
                    result = response.json()
                    assert (result["state"], result["error_code"]) == (expected_state, error)
                    assert client.get("/api/v1/knowledge/imports/" + result["id"]).json() == result
                too_big = client.post(
                    "/api/v1/knowledge/imports",
                    data={"section_id": section_id, "visibility": "public"},
                    files={"file": ("huge.txt", b"a" * (3 * 1024 * 1024), "text/plain")},
                )
                assert too_big.status_code == 422
                app.dependency_overrides[admin] = lambda: ctx.model_copy(
                    update={"tenant_id": other}
                )
                assert client.get("/api/v1/knowledge/imports/" + result["id"]).status_code == 404
                app.dependency_overrides[admin] = lambda: ctx
                app.dependency_overrides[tenant_context] = lambda: ctx.model_copy(
                    update={"tenant_id": other}
                )
                assert client.get(path).status_code == 404
                app.dependency_overrides.pop(admin)
                app.dependency_overrides[tenant_context] = lambda: ctx.model_copy(
                    update={"role": "specialist"}
                )
                assert (
                    client.post(
                        "/api/v1/knowledge/sections", json={"name": "Forbidden"}
                    ).status_code
                    == 403
                )
            db.close()
        finally:
            transaction.rollback()
    engine.dispose()


def test_concurrent_updates_reject_stale_revision():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from ips.core.config import Settings
    from ips.knowledge.repository import KnowledgeRepository
    from ips.knowledge.schemas import ArticleInput

    engine = create_engine(Settings().database_url)
    tenant, actor, section_id = uuid4(), uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO organizations (id,name) VALUES (:id,'race test')"), {"id": tenant}
        )
        connection.execute(
            text("INSERT INTO article_sections (id,tenant_id,name) VALUES (:id,:tenant,'test')"),
            {"id": section_id, "tenant": tenant},
        )
    try:
        with Session(engine) as db, db.begin():
            data = ArticleInput(
                section_id=section_id,
                title="Test",
                body="Original",
                visibility="public",
                status="published",
            )
            article_id = KnowledgeRepository().create_article(db, tenant, actor, data)["id"]
        barrier = Barrier(2, timeout=10)

        def edit(body):
            with Session(engine) as db:
                barrier.wait()
                try:
                    result = KnowledgeService(KnowledgeRepository()).update(
                        db, tenant, actor, article_id, 1, {"body": body}
                    )
                    db.commit()
                    return result["revision"]
                except HTTPException as error:
                    db.rollback()
                    return error.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(edit, ["First", "Second"])) == [2, 409]
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE articles SET current_revision_id=NULL WHERE tenant_id=:tenant"),
                {"tenant": tenant},
            )
            for table in ("knowledge_imports", "article_revisions", "articles", "article_sections"):
                connection.execute(
                    text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), {"tenant": tenant}
                )
            connection.execute(
                text("DELETE FROM organizations WHERE id=:tenant"), {"tenant": tenant}
            )
        engine.dispose()
