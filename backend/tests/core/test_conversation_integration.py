from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from ips.core.config import Settings
from ips.main import create_app


def test_real_app_connects_conversation_and_ticket_ports():
    app = create_app(Settings(widget_origin="https://widget.example"))
    with TestClient(app) as client:
        admin = client.post(
            "/api/v1/auth/register",
            headers={"Origin": "http://localhost:5173"},
            json={
                "organization_name": "Integration",
                "email": f"{uuid4()}@example.org",
                "password": "correct-horse-battery",
            },
        )
        assert admin.status_code == 201, admin.text
        widget = client.post(
            "/api/v1/widgets",
            headers={"Origin": "http://localhost:5173", "X-CSRF-Token": admin.json()["csrf_token"]},
            json={
                "name": "Widget",
                "color": "#123456",
                "greeting": "Hi",
                "allowed_origins": ["https://site.example"],
                "section_ids": [],
            },
        )
        assert widget.status_code == 201, widget.text
        key = widget.json()["public_key"]
        session = client.post(
            f"/api/v1/public/widgets/{key}/sessions",
            headers={"Origin": "https://site.example"},
            json={},
        )
        assert session.status_code == 201, session.text
        headers = {
            "Authorization": f"Bearer {session.json()['access_token']}",
            "Origin": "https://widget.example",
        }
        preflight = client.options(
            "/api/v1/public/conversations",
            headers={
                "Origin": "https://widget.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "https://widget.example"
        assert "access-control-allow-credentials" not in preflight.headers
        denied = client.options(
            "/api/v1/public/conversations",
            headers={
                "Origin": "https://attacker.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert denied.status_code == 403
        conversation = client.post("/api/v1/public/conversations", headers=headers, json={})
        assert conversation.status_code == 201, conversation.text
        assert conversation.headers["access-control-allow-origin"] == "https://widget.example"
        cid = conversation.json()["id"]
        message = client.post(
            f"/api/v1/public/conversations/{cid}/messages",
            headers=headers,
            json={"client_message_id": str(uuid4()), "text": "Hello"},
        )
        assert message.status_code == 201, message.text
        handoff = client.post(
            f"/api/v1/public/conversations/{cid}/handoff",
            headers=headers,
            json={"reason": "Please help"},
        )
        assert handoff.status_code == 200, handoff.text
        ticket = handoff.json()
        with app.state.engine.connect() as connection:
            state = connection.execute(
                text("SELECT mode,version FROM conversations WHERE id=:id"), {"id": UUID(cid)}
            ).one()
            before = connection.execute(
                text("SELECT count(*) FROM ai_runs WHERE conversation_id=:id"), {"id": UUID(cid)}
            ).scalar_one()
        assert state.mode == "human"
        next_message = client.post(
            f"/api/v1/public/conversations/{cid}/messages",
            headers=headers,
            json={"client_message_id": str(uuid4()), "text": "Still here"},
        )
        assert next_message.status_code == 201, next_message.text
        with app.state.engine.begin() as connection:
            after = connection.execute(
                text("SELECT count(*) FROM ai_runs WHERE conversation_id=:id"), {"id": UUID(cid)}
            ).scalar_one()
            connection.execute(
                text("UPDATE tickets SET status='resolved' WHERE id=:id"),
                {"id": UUID(ticket["id"])},
            )
        assert before == after == 1
        confirm = client.post(f"/api/v1/public/tickets/{ticket['id']}/confirm", headers=headers)
        assert confirm.status_code == 503
        with app.state.engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT status FROM tickets WHERE id=:id"), {"id": UUID(ticket["id"])}
                ).scalar_one()
                == "resolved"
            )
