from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ips.core.config import Settings
from ips.core.ports import TenantContext
from ips.tickets.assignment import claim_ticket


@pytest.fixture
def assignment_data():
    engine = create_engine(Settings().database_url)
    tenant = uuid4()
    users = [uuid4(), uuid4()]
    members = [uuid4(), uuid4()]
    tickets = [uuid4(), uuid4()]
    widget, customer = uuid4(), uuid4()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO organizations (id,name) VALUES (:id,'B05')"), {"id": tenant})
        for user, member in zip(users, members, strict=True):
            conn.execute(
                text(
                    "INSERT INTO users (id,normalized_email,password_hash) VALUES (:id,:email,'test')"
                ),
                {"id": user, "email": f"{user}@example.org"},
            )
            conn.execute(
                text(
                    "INSERT INTO memberships (id,tenant_id,user_id,role,max_active) VALUES (:id,:tenant,:user,'specialist',1)"
                ),
                {"id": member, "tenant": tenant, "user": user},
            )
        conn.execute(
            text(
                "INSERT INTO widgets (id,tenant_id,name,public_key,color,greeting,allowed_origins) VALUES (:id,:tenant,'Widget',:key,'#123456','Hello','{}')"
            ),
            {"id": widget, "tenant": tenant, "key": str(widget)},
        )
        conn.execute(
            text("INSERT INTO customers (id,tenant_id) VALUES (:id,:tenant)"),
            {"id": customer, "tenant": tenant},
        )
        for ticket in tickets:
            conn.execute(
                text(
                    "INSERT INTO conversations (id,tenant_id,widget_id,customer_id) VALUES (:id,:tenant,:widget,:customer)"
                ),
                {"id": ticket, "tenant": tenant, "widget": widget, "customer": customer},
            )
            conn.execute(
                text(
                    "INSERT INTO tickets (id,tenant_id,conversation_id,status,priority) VALUES (:id,:tenant,:conversation,'waiting_specialist','normal')"
                ),
                {"id": ticket, "tenant": tenant, "conversation": ticket},
            )
    contexts = [
        TenantContext(tenant_id=tenant, user_id=u, membership_id=m, role="specialist")
        for u, m in zip(users, members, strict=True)
    ]
    yield engine, tenant, contexts, tickets
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM tickets WHERE tenant_id=:tenant"), {"tenant": tenant})
        conn.execute(text("DELETE FROM conversations WHERE tenant_id=:tenant"), {"tenant": tenant})
        conn.execute(text("DELETE FROM customers WHERE tenant_id=:tenant"), {"tenant": tenant})
        conn.execute(text("DELETE FROM widgets WHERE tenant_id=:tenant"), {"tenant": tenant})
        conn.execute(text("DELETE FROM memberships WHERE tenant_id=:tenant"), {"tenant": tenant})
        conn.execute(text("DELETE FROM users WHERE id IN (:a,:b)"), {"a": users[0], "b": users[1]})
        conn.execute(text("DELETE FROM organizations WHERE id=:tenant"), {"tenant": tenant})
    engine.dispose()


def race(engine, pairs):
    start = Barrier(2, timeout=5)

    def run(pair):
        ctx, ticket = pair
        with Session(engine) as db:
            start.wait()
            try:
                value = claim_ticket(db, ctx, ticket, 1)
                db.commit()
                return value.status
            except HTTPException as error:
                db.rollback()
                return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(run, pairs))


def test_two_staff_claim_one_ticket_only_one_wins(assignment_data):
    engine, _, contexts, tickets = assignment_data
    assert sorted(
        map(str, race(engine, [(contexts[0], tickets[0]), (contexts[1], tickets[0])]))
    ) == ["409", "in_progress"]


def test_one_specialist_cannot_take_two_tickets_beyond_capacity(assignment_data):
    engine, _, contexts, tickets = assignment_data
    assert sorted(
        map(str, race(engine, [(contexts[0], tickets[0]), (contexts[0], tickets[1])]))
    ) == ["409", "in_progress"]


def test_claim_rejects_unavailable_and_foreign_tenant(assignment_data):
    engine, tenant, contexts, tickets = assignment_data
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE memberships SET available=false WHERE tenant_id=:tenant AND id=:id"),
            {"tenant": tenant, "id": contexts[0].membership_id},
        )
    with Session(engine) as db, pytest.raises(HTTPException) as err:
        claim_ticket(db, contexts[0], tickets[0], 1)
    assert err.value.status_code == 409
    foreign = contexts[1].model_copy(update={"tenant_id": uuid4()})
    with Session(engine) as db, pytest.raises(HTTPException) as err:
        claim_ticket(db, foreign, tickets[0], 1)
    assert err.value.status_code == 404


def test_routing_uses_available_specialist_in_matching_category(assignment_data):
    from ips.tickets.routing import route_ticket

    engine, tenant, contexts, tickets = assignment_data
    category = uuid4()
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO categories (id,tenant_id,name) VALUES (:id,:tenant,'category')"),
            {"id": category, "tenant": tenant},
        )
        conn.execute(
            text(
                "INSERT INTO specialist_categories (tenant_id,membership_id,category_id) VALUES (:tenant,:member,:category)"
            ),
            {"tenant": tenant, "member": contexts[0].membership_id, "category": category},
        )
        conn.execute(
            text("UPDATE tickets SET category_id=:category WHERE id=:ticket"),
            {"category": category, "ticket": tickets[0]},
        )
        conn.execute(
            text("UPDATE memberships SET available=false WHERE id=:member"),
            {"member": contexts[0].membership_id},
        )
    try:
        with Session(engine) as db, db.begin():
            assert route_ticket(db, tenant, tickets[0], 1) is None
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE memberships SET available=true WHERE id=:member"),
                {"member": contexts[0].membership_id},
            )
        with Session(engine) as db, db.begin():
            result = route_ticket(db, tenant, tickets[0], 1)
            assert result is not None and result.assignee_id == contexts[0].membership_id
            assert result.status == "in_progress"
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE tickets SET category_id=NULL WHERE id=:id"), {"id": tickets[0]}
            )
            conn.execute(
                text("DELETE FROM specialist_categories WHERE category_id=:id"), {"id": category}
            )
            conn.execute(text("DELETE FROM categories WHERE id=:id"), {"id": category})


def test_parallel_routing_respects_capacity_and_leaves_one_in_queue(assignment_data):
    from ips.tickets.routing import route_ticket

    engine, tenant, contexts, tickets = assignment_data
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE memberships SET available=false WHERE id=:id"),
            {"id": contexts[1].membership_id},
        )
    start = Barrier(2, timeout=5)

    def run(ticket):
        with Session(engine) as db:
            start.wait()
            result = route_ticket(db, tenant, ticket, 1)
            db.commit()
            return result.assignee_id if result else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, tickets))
    assert results.count(contexts[0].membership_id) == 1
    assert results.count(None) == 1


def test_reopen_and_claim_compete_for_same_capacity(assignment_data):
    from ips.core.ports import ConversationState, CustomerPrincipal
    from ips.tickets.service import TicketService

    engine, tenant, contexts, tickets = assignment_data
    with engine.begin() as conn:
        customer, widget, conversation = conn.execute(
            text("SELECT customer_id,widget_id,id FROM conversations WHERE id=:id"),
            {"id": tickets[0]},
        ).one()
        conn.execute(
            text(
                "UPDATE tickets SET conversation_id=:conversation, status='resolved', assignee_id=:member WHERE id=:ticket"
            ),
            {
                "conversation": conversation,
                "member": contexts[0].membership_id,
                "ticket": tickets[0],
            },
        )
    principal = CustomerPrincipal(tenant_id=tenant, customer_id=customer, widget_id=widget)
    state = ConversationState(
        id=conversation,
        tenant_id=tenant,
        customer_id=customer,
        widget_id=widget,
        mode="human",
        version=1,
    )

    class FakeConversations:
        def lock_for_handoff(self, db, tenant_id, conversation_id):
            assert tenant_id == tenant and conversation_id == conversation
            return state

        def mark_human(self, db, conversation_state):
            assert conversation_state == state

    start = Barrier(2, timeout=5)

    def run(operation):
        with Session(engine) as db:
            start.wait()
            try:
                if operation == "reopen":
                    TicketService(FakeConversations()).reopen(db, principal, tickets[0])
                else:
                    claim_ticket(db, contexts[0], tickets[1], 1)
                db.commit()
                return 200
            except HTTPException as error:
                db.rollback()
                return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, ["reopen", "claim"])) == [200, 409]
    with engine.connect() as conn:
        active = conn.execute(
            text(
                "SELECT count(*) FROM tickets WHERE tenant_id=:tenant AND assignee_id=:member AND status NOT IN ('resolved','closed')"
            ),
            {"tenant": tenant, "member": contexts[0].membership_id},
        ).scalar_one()
        assert active == 1


def test_reopen_rejects_when_assignee_is_already_at_capacity(assignment_data):
    from ips.core.ports import ConversationState, CustomerPrincipal
    from ips.tickets.service import TicketService

    engine, tenant, contexts, tickets = assignment_data
    with engine.begin() as conn:
        customer, widget, conversation = conn.execute(
            text("SELECT customer_id,widget_id,id FROM conversations WHERE id=:id"),
            {"id": tickets[0]},
        ).one()
        conn.execute(
            text(
                "UPDATE tickets SET conversation_id=:conversation,status='resolved',assignee_id=:member WHERE id=:ticket"
            ),
            {
                "conversation": conversation,
                "member": contexts[0].membership_id,
                "ticket": tickets[0],
            },
        )
        conn.execute(
            text("UPDATE tickets SET status='in_progress',assignee_id=:member WHERE id=:ticket"),
            {"member": contexts[0].membership_id, "ticket": tickets[1]},
        )
    principal = CustomerPrincipal(tenant_id=tenant, customer_id=customer, widget_id=widget)
    state = ConversationState(
        id=conversation,
        tenant_id=tenant,
        customer_id=customer,
        widget_id=widget,
        mode="human",
        version=1,
    )

    class FakeConversations:
        def lock_for_handoff(self, db, tenant_id, conversation_id):
            return state

        def mark_human(self, db, conversation_state):
            raise AssertionError("must not change mode on conflict")

    with Session(engine) as db, pytest.raises(HTTPException) as error:
        TicketService(FakeConversations()).reopen(db, principal, tickets[0])
    assert error.value.status_code == 409


def test_reassignment_permissions_capacity_and_release(assignment_data):
    from ips.tickets.assignment import reassign_ticket

    engine, _, contexts, tickets = assignment_data
    with Session(engine) as db, db.begin():
        claimed = claim_ticket(db, contexts[0], tickets[0], 1)
    with Session(engine) as db, db.begin():
        claim_ticket(db, contexts[1], tickets[1], 1)
    with Session(engine) as db, pytest.raises(HTTPException) as error:
        reassign_ticket(db, contexts[0], tickets[0], claimed.version, contexts[1].membership_id)
    assert error.value.status_code == 409
    with Session(engine) as db, pytest.raises(HTTPException) as error:
        reassign_ticket(db, contexts[1], tickets[0], claimed.version, None)
    assert error.value.status_code == 403
    with Session(engine) as db, db.begin():
        released = reassign_ticket(db, contexts[0], tickets[0], claimed.version, None)
    assert released.status == "waiting_specialist" and released.assignee_id is None
    assert released.version == claimed.version + 1
    admin = contexts[0].model_copy(update={"role": "admin"})
    with Session(engine) as db, db.begin():
        assigned = reassign_ticket(
            db, admin, tickets[0], released.version, contexts[0].membership_id
        )
    assert assigned.status == "in_progress" and assigned.assignee_id == contexts[0].membership_id


def test_claim_and_release_are_available_over_authenticated_http(assignment_data):
    from fastapi.testclient import TestClient

    from ips.main import create_app
    from ips.platform.auth import require_csrf, tenant_context

    _, _, contexts, tickets = assignment_data
    app = create_app(Settings())
    with TestClient(app) as client:
        claim_url = f"/api/v1/tickets/{tickets[0]}/claim"
        assert client.post(claim_url, json={"expected_version": 1}).status_code in (401, 422)
        app.dependency_overrides[tenant_context] = lambda: contexts[0]
        app.dependency_overrides[require_csrf] = lambda: None
        result = client.post(claim_url, json={"expected_version": 1})
        assert result.status_code == 200, result.text
        assert result.json()["assignee_id"] == str(contexts[0].membership_id)
        assert client.post(claim_url, json={"expected_version": 1}).status_code == 409
        url = f"/api/v1/tickets/{tickets[0]}"
        released = client.put(
            url, json={"expected_version": 2, "assignee_id": None, "priority": "high"}
        )
        assert released.status_code == 200, released.text
        assert released.json()["status"] == "waiting_specialist"
        assert released.json()["priority"] == "high"
        assert released.json()["version"] == 3


def test_reassign_and_claim_share_capacity_lock(assignment_data):
    from ips.tickets.assignment import reassign_ticket

    engine, tenant, contexts, tickets = assignment_data
    with Session(engine) as db, db.begin():
        claim_ticket(db, contexts[0], tickets[0], 1)
    start = Barrier(2, timeout=5)

    def run(operation):
        with Session(engine) as db:
            start.wait()
            try:
                if operation == "reassign":
                    reassign_ticket(db, contexts[0], tickets[0], 2, contexts[1].membership_id)
                else:
                    claim_ticket(db, contexts[1], tickets[1], 1)
                db.commit()
                return 200
            except HTTPException as error:
                db.rollback()
                return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, ["reassign", "claim"])) == [200, 409]
    with engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT count(*) FROM tickets WHERE tenant_id=:tenant AND assignee_id=:member AND status NOT IN ('resolved','closed')"
            ),
            {"tenant": tenant, "member": contexts[1].membership_id},
        ).scalar_one()
        assert count == 1


def test_real_handoff_assigns_available_specialist_and_repeat_is_idempotent(assignment_data):
    from ips.core.composition import default_ports
    from ips.core.ports import CustomerPrincipal

    engine, tenant, contexts, tickets = assignment_data
    with engine.begin() as conn:
        customer, widget = conn.execute(
            text("SELECT customer_id,widget_id FROM conversations WHERE id=:id"),
            {"id": tickets[0]},
        ).one()
        conn.execute(text("DELETE FROM tickets WHERE id=:id"), {"id": tickets[0]})
        conn.execute(
            text("UPDATE memberships SET available=false WHERE id=:id"),
            {"id": contexts[1].membership_id},
        )
    principal = CustomerPrincipal(tenant_id=tenant, customer_id=customer, widget_id=widget)
    tickets_port = default_ports().tickets
    with Session(engine) as db, db.begin():
        ticket = tickets_port.escalate(db, principal, tickets[0], "Help", None, "normal")
        assert ticket.assignee_id == contexts[0].membership_id
        assert ticket.status == "in_progress"
    with Session(engine) as db, db.begin():
        repeated = tickets_port.escalate(db, principal, tickets[0], "Help", None, "normal")
        assert repeated.id == ticket.id and repeated.version == ticket.version


def test_combined_reassignment_rejects_foreign_category_without_partial_update(assignment_data):
    from fastapi.testclient import TestClient

    from ips.main import create_app
    from ips.platform.auth import require_csrf, tenant_context

    engine, tenant, contexts, tickets = assignment_data
    with Session(engine) as db, db.begin():
        claim_ticket(db, contexts[0], tickets[0], 1)
    app = create_app(Settings())
    app.dependency_overrides[tenant_context] = lambda: contexts[0]
    app.dependency_overrides[require_csrf] = lambda: None
    with TestClient(app) as client:
        result = client.put(
            f"/api/v1/tickets/{tickets[0]}",
            json={"expected_version": 2, "assignee_id": None, "category_id": str(uuid4())},
        )
        assert result.status_code == 404
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT assignee_id,status,version FROM tickets WHERE tenant_id=:tenant AND id=:id"
            ),
            {"tenant": tenant, "id": tickets[0]},
        ).one()
        assert row.assignee_id == contexts[0].membership_id
        assert row.status == "in_progress" and row.version == 2


def test_real_handoff_stays_queued_without_available_specialists(assignment_data):
    from ips.core.composition import default_ports
    from ips.core.ports import CustomerPrincipal

    engine, tenant, _, tickets = assignment_data
    with engine.begin() as conn:
        customer, widget = conn.execute(
            text("SELECT customer_id,widget_id FROM conversations WHERE id=:id"),
            {"id": tickets[0]},
        ).one()
        conn.execute(text("DELETE FROM tickets WHERE id=:id"), {"id": tickets[0]})
        conn.execute(
            text("UPDATE memberships SET available=false WHERE tenant_id=:id"), {"id": tenant}
        )
    principal = CustomerPrincipal(tenant_id=tenant, customer_id=customer, widget_id=widget)
    with Session(engine) as db, db.begin():
        ticket = default_ports().tickets.escalate(db, principal, tickets[0], "Help", None, "normal")
        assert ticket.assignee_id is None and ticket.status == "waiting_specialist"


def test_reassignment_and_status_change_apply_together(assignment_data):
    from fastapi.testclient import TestClient

    from ips.main import create_app
    from ips.platform.auth import require_csrf, tenant_context

    engine, _, contexts, tickets = assignment_data
    with Session(engine) as db, db.begin():
        claim_ticket(db, contexts[0], tickets[0], 1)
    app = create_app(Settings())
    app.dependency_overrides[tenant_context] = lambda: contexts[0]
    app.dependency_overrides[require_csrf] = lambda: None
    with TestClient(app) as client:
        response = client.put(
            f"/api/v1/tickets/{tickets[0]}",
            json={
                "expected_version": 2,
                "assignee_id": str(contexts[1].membership_id),
                "status": "waiting_customer",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "waiting_customer"
        assert response.json()["assignee_id"] == str(contexts[1].membership_id)
        assert response.json()["version"] == 3


def test_resolve_cannot_release_assignee_before_reopen_and_claim(assignment_data):
    from fastapi.testclient import TestClient

    from ips.core.ports import CustomerPrincipal
    from ips.main import create_app
    from ips.platform.auth import require_csrf, tenant_context
    from ips.platform.widget_api import customer_principal

    engine, tenant, contexts, tickets = assignment_data
    with engine.connect() as conn:
        customer, widget, conversation = conn.execute(
            text("SELECT customer_id,widget_id,id FROM conversations WHERE id=:id"),
            {"id": tickets[0]},
        ).one()
    principal = CustomerPrincipal(tenant_id=tenant, customer_id=customer, widget_id=widget)
    with Session(engine) as db, db.begin():
        claim_ticket(db, contexts[0], tickets[0], 1)
    app = create_app(Settings())
    app.dependency_overrides[tenant_context] = lambda: contexts[0]
    app.dependency_overrides[require_csrf] = lambda: None
    app.dependency_overrides[customer_principal] = lambda: principal
    with TestClient(app) as client:
        path = f"/api/v1/tickets/{tickets[0]}"
        invalid = client.put(
            path, json={"expected_version": 2, "assignee_id": None, "status": "resolved"}
        )
        assert invalid.status_code == 409
        current = client.get(path).json()
        assert current["status"] == "in_progress" and current["version"] == 2
        assert current["assignee_id"] == str(contexts[0].membership_id)
        resolved = client.put(path, json={"expected_version": 2, "status": "resolved"})
        assert resolved.status_code == 200, resolved.text
        reopened = client.post(f"/api/v1/public/tickets/{tickets[0]}/reopen")
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["assignee_id"] == str(contexts[0].membership_id)
        claim = client.post(f"{path}/claim", json={"expected_version": reopened.json()["version"]})
        assert claim.status_code == 200, claim.text
        assert claim.json()["assignee_id"] == str(contexts[0].membership_id)
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT mode FROM conversations WHERE id=:id"), {"id": conversation}
                ).scalar_one()
                == "human"
            )
