from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException

from ips.conversations.service import ConversationService
from ips.core.ports import CustomerPrincipal


class FakeRepository:
    def __init__(self):
        self.conversations = {}
        self.messages = {}
        self.runs = []

    def find_customer(self, uow, principal, conversation_id, *, lock=False):
        row = self.conversations.get(conversation_id)
        if row is None or (row["tenant_id"], row["customer_id"], row["widget_id"]) != (
            principal.tenant_id,
            principal.customer_id,
            principal.widget_id,
        ):
            return None
        if principal.conversation_scope and principal.conversation_scope != conversation_id:
            return None
        return row

    def find_message(self, uow, tenant_id, conversation_id, client_message_id):
        return next(
            (
                row
                for row in self.messages.get(conversation_id, [])
                if row["tenant_id"] == tenant_id and row["client_message_id"] == client_message_id
            ),
            None,
        )

    def append_message(
        self, uow, tenant_id, conversation_id, kind, text, client_message_id, author_id=None
    ):
        entries = self.messages.setdefault(conversation_id, [])
        row = {
            "id": uuid4(),
            "tenant_id": tenant_id,
            "conversation_id": conversation_id,
            "seq": len(entries) + 1,
            "kind": kind,
            "text": text,
            "client_message_id": client_message_id,
            "author_id": author_id,
            "sources": [],
            "created_at": datetime.now(UTC),
            "attachment_ids": [],
        }
        entries.append(row)
        return row

    def create_ai_run(self, uow, tenant_id, conversation_id, message_id, version):
        self.runs.append((tenant_id, conversation_id, message_id, version))

    def list_messages(self, uow, tenant_id, conversation_id, after_seq, limit, *, public):
        return [
            m
            for m in self.messages.get(conversation_id, [])
            if m["tenant_id"] == tenant_id
            and m["seq"] > after_seq
            and (not public or m["kind"] in {"customer", "assistant", "specialist"})
        ][:limit]


def test_conversation_routes_match_openapi():
    import json
    from pathlib import Path

    from fastapi import FastAPI

    from ips.conversations.router import public_router, staff_router
    from ips.core.export_openapi import compare_contracts

    app = FastAPI()
    app.include_router(public_router)
    app.include_router(staff_router)
    contract_path = Path(__file__).resolve().parents[3] / "contracts" / "openapi.json"
    expected = json.loads(contract_path.read_text(encoding="utf-8"))
    assert compare_contracts(expected, app.openapi()) == []


@pytest.fixture
def setup_conversations():
    repo = FakeRepository()
    principal = CustomerPrincipal(tenant_id=uuid4(), customer_id=uuid4(), widget_id=uuid4())
    a, b = uuid4(), uuid4()
    for identifier in (a, b):
        repo.conversations[identifier] = {
            "id": identifier,
            "tenant_id": principal.tenant_id,
            "customer_id": principal.customer_id,
            "widget_id": principal.widget_id,
            "mode": "bot",
            "version": 1,
        }
    return repo, principal, a, b


def test_idempotency_and_conversation_isolation(setup_conversations):
    repo, principal, a, b = setup_conversations
    service = ConversationService(repo)
    key = uuid4()
    first, created = service.send_customer(None, principal, a, key, "hello")
    again, created_again = service.send_customer(None, principal, a, key, "hello")
    assert created and not created_again and again == first
    service.send_customer(None, principal, b, uuid4(), "other")
    assert [item.text for item in service.customer_messages(None, principal, a, 0, 30)] == ["hello"]
    assert len(repo.runs) == 2
    with pytest.raises(HTTPException) as error:
        service.send_customer(None, principal, a, key, "changed")
    assert error.value.status_code == 409


def test_customer_scope_and_public_projection(setup_conversations):
    repo, principal, a, b = setup_conversations
    service = ConversationService(repo)
    repo.append_message(None, principal.tenant_id, a, "internal_note", "private", uuid4())
    repo.append_message(None, principal.tenant_id, a, "specialist", "public", uuid4())
    assert [m.text for m in service.customer_messages(None, principal, a, 0, 30)] == ["public"]
    other = principal.model_copy(update={"customer_id": uuid4()})
    scoped = principal.model_copy(update={"conversation_scope": b})
    for unauthorized in (other, scoped, principal.model_copy(update={"tenant_id": uuid4()})):
        with pytest.raises(HTTPException) as error:
            service.customer_messages(None, unauthorized, a, 0, 30)
        assert error.value.status_code == 404


def test_postgres_message_isolation_and_handoff():
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from ips.conversations.repository import ConversationRepository
    from ips.core.config import Settings

    engine = create_engine(Settings().database_url)
    tenant, widget, customer, stranger = (uuid4() for _ in range(4))
    principal = CustomerPrincipal(tenant_id=tenant, widget_id=widget, customer_id=customer)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            db = Session(bind=connection)
            db.execute(
                text("INSERT INTO organizations (id,name) VALUES (:id,'test')"), {"id": tenant}
            )
            db.execute(
                text("""INSERT INTO widgets
                (id, tenant_id, name, public_key, color, greeting, allowed_origins)
                VALUES (:id,:tenant,'test',:key,'#ffffff','hello',ARRAY['https://example.test'])"""),
                {"id": widget, "tenant": tenant, "key": str(uuid4())},
            )
            for cid in (customer, stranger):
                db.execute(
                    text("INSERT INTO customers (id,tenant_id) VALUES (:id,:tenant)"),
                    {"id": cid, "tenant": tenant},
                )
            repo = ConversationRepository()
            service = ConversationService(repo)
            first, second = repo.create(db, principal), repo.create(db, principal)
            first_id, second_id = first["id"], second["id"]
            key = uuid4()
            message, created = service.send_customer(db, principal, first_id, key, "hello")
            assert created
            duplicate, created = service.send_customer(db, principal, first_id, key, "hello")
            assert not created and duplicate.id == message.id
            with pytest.raises(HTTPException) as conflict:
                service.send_customer(db, principal, first_id, key, "changed")
            assert conflict.value.status_code == 409
            service.send_customer(db, principal, second_id, uuid4(), "other")
            repo.append_message(db, tenant, first_id, "internal_note", "secret", uuid4())
            public = service.customer_messages(db, principal, first_id, 0, 30)
            assert [m.text for m in public] == ["hello"]
            with pytest.raises(HTTPException) as denied:
                service.customer_messages(
                    db, principal.model_copy(update={"customer_id": stranger}), first_id, 0, 30
                )
            assert denied.value.status_code == 404
            state = service.lock_for_handoff(db, tenant, first_id)
            service.mark_human(db, state)
            service.send_customer(db, principal, first_id, uuid4(), "human please")
            assert (
                db.execute(
                    text(
                        "SELECT count(*) FROM ai_runs WHERE tenant_id=:tenant AND conversation_id=:id"
                    ),
                    {"tenant": tenant, "id": first_id},
                ).scalar_one()
                == 1
            )
            db.close()
        finally:
            transaction.rollback()
    engine.dispose()


def test_postgres_http_contract():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from ips.conversations.router import public_router, staff_router
    from ips.core.config import Settings
    from ips.core.db import get_session
    from ips.core.ports import TenantContext
    from ips.platform.auth import require_csrf, tenant_context
    from ips.platform.widget_api import customer_principal

    engine = create_engine(Settings().database_url)
    tenant, widget, customer, other = (uuid4() for _ in range(4))
    principal = CustomerPrincipal(tenant_id=tenant, widget_id=widget, customer_id=customer)
    ctx = TenantContext(tenant_id=tenant, user_id=uuid4(), membership_id=uuid4(), role="admin")
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            db = Session(bind=connection)
            db.execute(
                text("INSERT INTO organizations (id,name) VALUES (:id,'test')"), {"id": tenant}
            )
            db.execute(
                text("""INSERT INTO widgets
                (id, tenant_id, name, public_key, color, greeting, allowed_origins)
                VALUES (:id,:tenant,'test',:key,'#ffffff','hello',ARRAY['https://example.test'])"""),
                {"id": widget, "tenant": tenant, "key": str(uuid4())},
            )
            for cid in (customer, other):
                db.execute(
                    text("INSERT INTO customers (id,tenant_id) VALUES (:id,:tenant)"),
                    {"id": cid, "tenant": tenant},
                )
            app = FastAPI()
            app.state.ports = None
            app.include_router(public_router)
            app.include_router(staff_router)
            app.dependency_overrides[get_session] = lambda: db
            app.dependency_overrides[customer_principal] = lambda: principal
            app.dependency_overrides[tenant_context] = lambda: ctx
            app.dependency_overrides[require_csrf] = lambda: None
            with TestClient(app) as client:
                created = client.post("/api/v1/public/conversations", json={})
                assert created.status_code == 201, created.text
                conversation_id = created.json()["id"]
                initial = client.post(
                    "/api/v1/public/conversations", json={"initial_text": "first"}
                )
                assert initial.status_code == 201, initial.text
                listing = client.get("/api/v1/public/conversations", params={"limit": 1})
                assert listing.status_code == 200, listing.text
                assert len(listing.json()["items"]) == 1
                next_cursor = listing.json()["next_cursor"]
                assert next_cursor is not None
                next_page = client.get(
                    "/api/v1/public/conversations", params={"cursor": next_cursor}
                )
                assert len(next_page.json()["items"]) == 1
                path = f"/api/v1/public/conversations/{conversation_id}/messages"
                message = {"client_message_id": str(uuid4()), "text": "hello"}
                first = client.post(path, json=message)
                assert first.status_code == 201, first.text
                again = client.post(path, json=message)
                assert again.status_code == 200 and first.json() == again.json()
                assert client.post(path, json={**message, "text": "changed"}).status_code == 409
                staff_path = f"/api/v1/conversations/{conversation_id}/messages"
                note = client.post(
                    staff_path,
                    json={"client_message_id": str(uuid4()), "text": "secret", "internal": True},
                )
                assert note.status_code == 201, note.text
                staff_page = client.get(staff_path)
                assert staff_page.status_code == 200, staff_page.text
                assert [m["text"] for m in staff_page.json()["items"]] == ["hello", "secret"]
                assert [m["text"] for m in client.get(path).json()["items"]] == ["hello"]
                app.dependency_overrides[tenant_context] = lambda: ctx.model_copy(
                    update={"tenant_id": uuid4()}
                )
                assert client.get(staff_path).status_code == 404
                app.dependency_overrides[customer_principal] = lambda: principal.model_copy(
                    update={"customer_id": other}
                )
                assert client.get(path).status_code == 404
            db.close()
        finally:
            transaction.rollback()
    engine.dispose()


def test_concurrent_retries_create_one_message_and_one_run():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from ips.conversations.repository import ConversationRepository
    from ips.core.config import Settings

    engine = create_engine(Settings().database_url)
    tenant, widget, customer = (uuid4() for _ in range(3))
    principal = CustomerPrincipal(tenant_id=tenant, widget_id=widget, customer_id=customer)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO organizations (id,name) VALUES (:id,'test')"), {"id": tenant}
        )
        connection.execute(
            text("""INSERT INTO widgets
            (id,tenant_id,name,public_key,color,greeting,allowed_origins)
            VALUES (:id,:tenant,'test',:key,'#ffffff','hello',ARRAY['https://example.test'])"""),
            {"id": widget, "tenant": tenant, "key": str(uuid4())},
        )
        connection.execute(
            text("INSERT INTO customers (id,tenant_id) VALUES (:id,:tenant)"),
            {"id": customer, "tenant": tenant},
        )
    try:
        with Session(engine) as db, db.begin():
            conversation_id = ConversationRepository().create(db, principal)["id"]
        barrier = Barrier(2, timeout=10)
        message_id = uuid4()

        def send():
            with Session(engine) as db:
                barrier.wait()
                result, created = ConversationService(ConversationRepository()).send_customer(
                    db, principal, conversation_id, message_id, "hello"
                )
                db.commit()
                return result.id, created

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: send(), range(2)))
        assert outcomes[0][0] == outcomes[1][0]
        assert sorted(created for _, created in outcomes) == [False, True]
        with engine.connect() as connection:
            for table in ("messages", "ai_runs"):
                assert (
                    connection.execute(
                        text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                        {"tenant": tenant},
                    ).scalar_one()
                    == 1
                )
    finally:
        with engine.begin() as connection:
            for table in ("ai_runs", "messages", "conversations", "customers", "widgets"):
                connection.execute(
                    text(f"DELETE FROM {table} WHERE tenant_id=:tenant"), {"tenant": tenant}
                )
            connection.execute(
                text("DELETE FROM organizations WHERE id=:tenant"), {"tenant": tenant}
            )
        engine.dispose()
