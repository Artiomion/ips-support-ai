from concurrent.futures import ThreadPoolExecutor
from email import policy
from email.parser import BytesParser
from threading import Barrier
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from test_smtp import smtp_inbox

from ips.core.config import Settings
from ips.core.jobs import drain_once
from ips.main import create_app
from ips.platform.email_links import link_token
from ips.platform.notifications import notification_handlers
from ips.platform.widget_api import token_hash


@pytest.fixture
def scenario():
    cfg = Settings(widget_origin="https://widget.example")
    app = create_app(cfg)
    with TestClient(app) as client:
        admin = client.post(
            "/api/v1/auth/register",
            headers={"Origin": cfg.web_origin},
            json={
                "organization_name": "Email tests",
                "email": f"{uuid4()}@example.org",
                "password": "correct-horse-battery",
            },
        )
        assert admin.status_code == 201
        staff = {"Origin": cfg.web_origin, "X-CSRF-Token": admin.json()["csrf_token"]}
        widget = client.post(
            "/api/v1/widgets",
            headers=staff,
            json={
                "name": "Support",
                "color": "#123456",
                "greeting": "Hello",
                "allowed_origins": ["https://shop.example"],
                "section_ids": [],
            },
        ).json()
        session = client.post(
            f"/api/v1/public/widgets/{widget['public_key']}/sessions",
            headers={"Origin": "https://shop.example"},
            json={},
        ).json()
        customer = bearer(session["access_token"], cfg.widget_origin)
        first, second = [
            client.post("/api/v1/public/conversations", headers=customer, json={}).json()["id"]
            for _ in range(2)
        ]
        yield app, client, staff, widget["id"], customer, first, second


def bearer(access_token, origin):
    return {"Authorization": f"Bearer {access_token}", "Origin": origin}


def email_request(client, customer, conversation, email):
    return client.post(
        "/api/v1/public/customer/email/request",
        headers=customer,
        json={"conversation_id": conversation, "email": email},
    )


def send_staff_message(client, staff, conversation_id, *, internal=False):
    body = {"client_message_id": str(uuid4()), "text": "Synthetic reply body", "internal": internal}
    path = f"/api/v1/conversations/{conversation_id}/messages"
    assert client.post(path, headers=staff, json=body).status_code == 201
    assert client.post(path, headers=staff, json=body).status_code == 200


def drain_mail(engine, settings, smtp, kind, recipient):
    settings = settings.model_copy(
        update={"smtp_host": "127.0.0.1", "smtp_port": smtp.server_address[1]}
    )
    handler = notification_handlers(engine, settings)[kind]
    while drain_once(engine, {kind: handler}):
        pass
    messages = []
    while not smtp.messages.empty():
        message = BytesParser(policy=policy.default).parsebytes(smtp.messages.get_nowait())
        if message["To"] == recipient:
            messages.append(message)
    return messages


def test_email_confirmation_and_reply_link_each_grant_only_one_conversation(scenario):
    app, client, staff, _, customer, first, second = scenario
    settings = app.state.settings
    recipient = f"{uuid4()}@example.org"
    path = "/api/v1/public/customer/email/request"
    preflight = client.options(
        path,
        headers={
            "Origin": settings.widget_origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert preflight.status_code == 200
    assert "access-control-allow-credentials" not in preflight.headers
    ticket_path = f"/api/v1/public/conversations/{first}/ticket"
    handoff = client.post(
        f"/api/v1/public/conversations/{first}/handoff",
        headers=customer,
        json={"reason": "Help"},
    )
    assert handoff.status_code == 200
    existing_ticket = handoff.json()
    send_staff_message(client, staff, first)
    queued = email_request(client, customer, first, recipient)
    assert queued.status_code == 202 and queued.json() == {"state": "queued"}

    with smtp_inbox() as smtp:
        [mail] = drain_mail(app.state.engine, settings, smtp, "customer_email", recipient)
    fragment = parse_qs(urlsplit(mail.get_content().split()[-1]).fragment)
    assert fragment["action"] == ["confirm"]
    token = fragment["token"][0]
    with app.state.engine.connect() as conn:
        row = conn.execute(
            text("""SELECT token_hash, expires_at-created_at AS ttl
            FROM customer_links WHERE conversation_id=:id"""),
            {"id": first},
        ).one()
        assert row.token_hash == token_hash(token) and row.ttl.total_seconds() == 1800
        assert not conn.execute(
            text("SELECT EXISTS(SELECT 1 FROM jobs WHERE strpos(payload::text,:token)>0)"),
            {"token": token},
        ).scalar_one()

    origin = {"Origin": settings.widget_origin}
    request = {"json": {"token": token}, "headers": origin}
    assert client.post("/api/v1/public/customer/links/exchange", **request).status_code == 404
    confirm = "/api/v1/public/customer/email/confirm"
    response = client.post(confirm, **request)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["conversation_id"] == first
    assert client.post(confirm, **request).status_code == 404
    scoped = bearer(response.json()["access_token"], settings.widget_origin)
    assert client.get("/api/v1/public/session", headers=scoped).status_code == 200
    assert (
        client.get(f"/api/v1/public/conversations/{first}/messages", headers=scoped).status_code
        == 200
    )
    assert (
        client.get(f"/api/v1/public/conversations/{second}/messages", headers=scoped).status_code
        == 404
    )
    assert email_request(client, scoped, second, recipient).status_code == 404

    send_staff_message(client, staff, first, internal=True)
    send_staff_message(client, staff, second)
    send_staff_message(client, staff, first)
    with smtp_inbox() as smtp:
        [reply] = drain_mail(app.state.engine, settings, smtp, "staff_reply", recipient)
    assert "Synthetic reply body" not in reply.get_content()
    continuation = parse_qs(urlsplit(reply.get_content().split()[-1]).fragment)
    assert continuation["action"] == ["exchange"]
    exchange = "/api/v1/public/customer/links/exchange"
    payload = {"token": continuation["token"][0]}
    barrier = Barrier(2, timeout=10)

    def consume_on_new_device():
        with TestClient(app) as device:
            barrier.wait()
            return device.post(exchange, headers=origin, json=payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: consume_on_new_device(), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 404]
    success = next(response for response in responses if response.status_code == 200)
    assert success.json()["conversation_id"] == first
    scoped = bearer(success.json()["access_token"], settings.widget_origin)
    with TestClient(app) as new_browser:
        restored = new_browser.get(ticket_path, headers=scoped)
        assert restored.status_code == 200
        assert restored.json()["ticket"]["id"] == existing_ticket["id"]
        assert restored.json()["ticket"]["status"] == existing_ticket["status"]
        assert restored.headers["cache-control"] == "no-store"
        assert (
            new_browser.get(
                f"/api/v1/public/conversations/{second}/ticket", headers=scoped
            ).status_code
            == 404
        )
    assert (
        client.get(f"/api/v1/public/conversations/{second}/messages", headers=scoped).status_code
        == 404
    )
    assert client.post(exchange, headers=origin, json=payload).status_code == 404


def test_conversation_ticket_is_a_scoped_read_of_the_current_lifecycle(scenario):
    app, client, _, widget_id, customer, first, _ = scenario
    path = f"/api/v1/public/conversations/{first}/ticket"
    assert client.get(path).status_code == 401
    empty = client.get(path, headers=customer)
    assert empty.status_code == 200 and empty.json() == {"ticket": None}
    origin = app.state.settings.widget_origin
    preflight = {
        "Origin": origin,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "Authorization",
    }
    allowed = client.options(path, headers=preflight)
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-credentials" not in allowed.headers
    assert (
        client.options(path, headers={**preflight, "Origin": "https://shop.example"}).status_code
        == 403
    )

    with app.state.engine.connect() as conn:
        assert (
            conn.scalar(
                text("SELECT count(*) FROM tickets WHERE conversation_id=:id"), {"id": first}
            )
            == 0
        )
        public_key = conn.scalar(
            text("SELECT public_key FROM widgets WHERE id=:id"), {"id": widget_id}
        )
    new_session = client.post(
        f"/api/v1/public/widgets/{public_key}/sessions",
        headers={"Origin": "https://shop.example"},
        json={},
    )
    assert new_session.status_code == 201
    other_customer = bearer(new_session.json()["access_token"], origin)
    foreign = client.get(path, headers=other_customer)
    unknown = client.get(f"/api/v1/public/conversations/{uuid4()}/ticket", headers=other_customer)
    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json()["error"]["code"] == unknown.json()["error"]["code"]

    handoff_path = f"/api/v1/public/conversations/{first}/handoff"
    handoff = client.post(handoff_path, headers=customer, json={"reason": "Help"})
    assert handoff.status_code == 200
    older = handoff.json()["id"]
    # Use a real resolved ticket and the existing customer confirmation endpoint.
    with app.state.engine.begin() as conn:
        conn.execute(
            text("UPDATE tickets SET status='resolved',resolved_at=now() WHERE id=:id"),
            {"id": older},
        )
    resolved = client.get(path, headers=customer).json()["ticket"]
    assert (resolved["id"], resolved["status"]) == (older, "resolved")
    assert (
        client.post(f"/api/v1/public/tickets/{older}/confirm", headers=customer).status_code == 200
    )
    assert client.get(path, headers=customer).json()["ticket"]["status"] == "closed"

    another = client.post(handoff_path, headers=customer, json={"reason": "Another request"})
    assert another.status_code == 200
    latest = another.json()["id"]
    assert latest != older
    with app.state.engine.begin() as conn:
        conn.execute(
            text("UPDATE tickets SET created_at=now()-interval '1 day' WHERE id=:id"),
            {"id": latest},
        )
    assert client.get(path, headers=customer).json()["ticket"]["id"] == latest
    with app.state.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE tickets SET status='resolved',resolved_at=now()+interval '1 second' WHERE id=:id"
            ),
            {"id": latest},
        )
    assert client.get(path, headers=customer).json()["ticket"]["id"] == latest


def test_expired_confirmation_has_same_error_as_unknown_token(scenario):
    app, client, _, _, customer, first, _ = scenario
    assert email_request(client, customer, first, f"{uuid4()}@example.org").status_code == 202
    with app.state.engine.begin() as conn:
        link_id = conn.execute(
            text(
                """UPDATE customer_links SET expires_at=now() - interval '1 second'
                WHERE conversation_id=:id RETURNING id"""
            ),
            {"id": first},
        ).scalar_one()
    expired_token = link_token(app.state.settings.session_secret, "confirm", link_id)
    path = "/api/v1/public/customer/email/confirm"
    expired = client.post(path, json={"token": expired_token})
    unknown = client.post(path, json={"token": uuid4().hex})
    assert expired.status_code == unknown.status_code == 404
    assert expired.json()["error"]["code"] == unknown.json()["error"]["code"]
    assert expired.json()["error"]["message"] == unknown.json()["error"]["message"]


def test_rating_and_widget_overview(scenario):
    app, client, staff, widget, customer, first, second = scenario
    overview = "/api/v1/analytics/overview"
    empty = client.get(overview, params={"widget_id": str(uuid4())})
    assert empty.status_code == 404
    initial = client.get(overview, params={"widget_id": widget}).json()
    assert initial["conversations"] == 2 and initial["ai_confirmed"] == 0
    assert initial["first_response_seconds_avg"] is None
    assert initial["resolution_seconds_avg"] is None
    assert client.get(overview, headers={**customer, "Cookie": ""}).status_code == 401
    with TestClient(app) as outsider:
        organization = outsider.post(
            "/api/v1/auth/register",
            headers={"Origin": staff["Origin"]},
            json={
                "organization_name": "Unrelated organization",
                "email": f"{uuid4()}@example.org",
                "password": "correct-horse-battery",
            },
        )
        assert organization.status_code == 201
        assert outsider.get(overview, params={"widget_id": widget}).status_code == 404
        assert outsider.get(overview).json()["conversations"] == 0

    handoff = client.post(
        f"/api/v1/public/conversations/{first}/handoff", headers=customer, json={"reason": "Help"}
    ).json()
    rating = f"/api/v1/public/tickets/{handoff['id']}/rating"
    assert client.post(rating, headers=customer, json={"score": 5}).status_code == 409
    for score in (0, 6, True):
        assert client.post(rating, headers=customer, json={"score": score}).status_code == 422
    for comment in (None, "x" * 2001, "\x00"):
        assert (
            client.post(rating, headers=customer, json={"score": 4, "comment": comment}).status_code
            == 422
        )

    second_path = f"/api/v1/public/conversations/{second}"
    question = client.post(
        f"{second_path}/messages",
        headers=customer,
        json={"client_message_id": str(uuid4()), "text": "Synthetic question"},
    )
    assert question.status_code == 201
    with app.state.engine.begin() as conn:
        conn.execute(
            text("""UPDATE tickets SET status='resolved',
            first_staff_reply_at=created_at+interval '10 seconds',
            resolved_at=created_at+interval '20 seconds' WHERE id=:id"""),
            {"id": handoff["id"]},
        )
        conn.execute(
            text("""WITH completed AS (
                UPDATE ai_runs SET state='completed' WHERE input_message_id=:question
                RETURNING tenant_id, conversation_id, id
            )
            INSERT INTO messages (id, tenant_id, conversation_id, seq, kind, text, client_message_id)
            SELECT :id, tenant_id, conversation_id, 2, 'assistant', 'Synthetic answer', id
            FROM completed"""),
            {"question": question.json()["id"], "id": uuid4()},
        )
    assert client.get(overview, params={"widget_id": widget}).json()["ai_confirmed"] == 0
    assert client.post(f"{second_path}/confirm-ai", headers=customer).status_code == 200
    response = client.post(rating, headers=customer, json={"score": 5})
    assert response.status_code == 200 and response.json() == {"score": 5}
    assert client.post(rating, headers=customer, json={"score": 3}).status_code == 409
    result = client.get(overview, headers=staff, params={"widget_id": widget}).json()
    assert (result["tickets"], result["handoffs"], result["ai_confirmed"]) == (1, 1, 1)
    assert (result["first_response_seconds_avg"], result["resolution_seconds_avg"]) == (10, 20)
