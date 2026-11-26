"""Use real API processes and independent PostgreSQL databases for the M4 smoke."""

import os
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import httpx
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from demo_seed import seed_demo
from sqlalchemy import create_engine, make_url, text

from ips.core.config import Settings

BACKEND = Path(__file__).resolve().parents[2]
M3_REVISION = "b0008_request_limits"
WEB_ORIGIN = "http://localhost:5173"
WIDGET_ORIGIN = "http://localhost:5174"
PASSWORD = "local-test-password"
UVICORN_ORIGIN = re.compile(rb"Uvicorn running on (http://127\.0\.0\.1:\d+)")


@pytest.fixture
def databases():
    url = make_url(Settings().database_url)
    control = create_engine(url, isolation_level="AUTOCOMMIT")
    created = []

    def create():
        # Internally generated identifiers contain only lowercase letters and digits.
        name = f"ips_test_{uuid4().hex}"
        with control.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        created.append(name)
        return url.set(database=name)

    try:
        yield create
    finally:
        with control.connect() as conn:
            for name in created:
                conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        control.dispose()


def process_env(database):
    return {
        **os.environ,
        "DATABASE_URL": database.render_as_string(hide_password=False),
        "APP_ENV": "development",
        "COOKIE_SECURE": "false",
        "WEB_ORIGIN": WEB_ORIGIN,
        "WIDGET_ORIGIN": WIDGET_ORIGIN,
        "AI_RUNNER_ENABLED": "false",
    }


def migrate(database, revision="head"):
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=BACKEND,
        env=process_env(database),
        check=True,
        timeout=30,
    )


@contextmanager
def api_process(database, log):
    with log.open("wb") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "ips.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "0",
                "--log-level",
                "info",
                "--no-use-colors",
            ],
            cwd=BACKEND,
            env=process_env(database),
            stdout=output,
            stderr=subprocess.STDOUT,
        )
    try:
        deadline = monotonic() + 15
        # Let Uvicorn bind an ephemeral port itself on both Windows and Unix.
        while process.poll() is None and monotonic() < deadline:
            match = UVICORN_ORIGIN.search(log.read_bytes())
            if match is not None:
                origin = match[1].decode("ascii")
                break
            sleep(0.05)
        else:
            pytest.fail(
                f"API did not bind a port:\n{log.read_text(encoding='utf-8', errors='replace')}"
            )

        with httpx.Client(base_url=origin, timeout=5, trust_env=False) as client:
            while process.poll() is None and monotonic() < deadline:
                try:
                    if client.get("/health/ready", timeout=0.5).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                sleep(0.05)
            else:
                pytest.fail(
                    f"API did not start:\n{log.read_text(encoding='utf-8', errors='replace')}"
                )
            yield client
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def staff_login(client, email):
    result = client.post(
        "/api/v1/auth/login",
        headers={"Origin": WEB_ORIGIN},
        json={"email": email, "password": PASSWORD},
    )
    assert result.status_code == 200


def start_conversation(client, widget_key):
    response = client.post(
        f"/api/v1/public/widgets/{widget_key}/sessions", headers={"Origin": WEB_ORIGIN}, json={}
    )
    assert response.status_code == 201
    customer = {
        "Origin": WIDGET_ORIGIN,
        "Authorization": f"Bearer {response.json()['access_token']}",
    }
    conversation = client.post("/api/v1/public/conversations", headers=customer, json={})
    assert conversation.status_code == 201
    assert conversation.json()["mode"] == "bot"
    return customer, conversation.json()["id"]


def test_handoff_rolls_back_and_survives_api_restart(databases, tmp_path):
    database = databases()
    migrate(database)
    engine = create_engine(database)
    try:
        with api_process(database, tmp_path / "api.log") as client:
            alpha, beta = seed_demo(str(client.base_url), WEB_ORIGIN, PASSWORD)
            assert seed_demo(str(client.base_url), WEB_ORIGIN, PASSWORD) == [alpha, beta]
            headers, conversation = start_conversation(client, alpha["public_key"])
            staff_login(client, alpha["email"])
            path = f"/api/v1/public/conversations/{conversation}/handoff"
            with engine.begin() as conn:
                conn.execute(
                    text("""CREATE FUNCTION reject_handoff() RETURNS trigger
                    LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'test handoff rollback'; END $$""")
                )
                conn.execute(
                    text("""CREATE TRIGGER reject_handoff
                    BEFORE UPDATE OF mode ON conversations FOR EACH ROW
                    WHEN (NEW.mode = 'human') EXECUTE FUNCTION reject_handoff()""")
                )
            # Uvicorn closes the connection after an unhandled application error.
            failed = client.post(
                path, headers={**headers, "Connection": "close"}, json={"reason": "Test handoff"}
            )
            assert failed.status_code == 500
            with engine.begin() as conn:
                assert (
                    conn.scalar(
                        text("SELECT mode FROM conversations WHERE id=:id"), {"id": conversation}
                    )
                    == "bot"
                )
                assert (
                    conn.scalar(
                        text("SELECT count(*) FROM tickets WHERE conversation_id=:id"),
                        {"id": conversation},
                    )
                    == 0
                )
                conn.execute(text("DROP TRIGGER reject_handoff ON conversations"))
            success = client.post(path, headers=headers, json={"reason": "Test handoff"})
            assert success.status_code == 200
            ticket = success.json()["id"]
            assert (
                client.post(path, headers=headers, json={"reason": "Retry"}).json()["id"] == ticket
            )
            with engine.connect() as conn:
                assert (
                    conn.scalar(
                        text("SELECT mode FROM conversations WHERE id=:id"), {"id": conversation}
                    )
                    == "human"
                )
                assert (
                    conn.scalar(
                        text("SELECT count(*) FROM tickets WHERE conversation_id=:id"),
                        {"id": conversation},
                    )
                    == 1
                )

        with api_process(database, tmp_path / "restarted.log") as client:
            public = client.get(f"/api/v1/public/tickets/{ticket}", headers=headers)
            assert public.status_code == 200 and public.json()["conversation_id"] == conversation
            staff_login(client, beta["email"])
            assert client.get(f"/api/v1/tickets/{ticket}").status_code == 404
            staff_login(client, alpha["email"])
            assert client.get(f"/api/v1/tickets/{ticket}").status_code == 200
    finally:
        engine.dispose()


def pg_tool(database, *args, **streams):
    env = {**os.environ, "PGDATABASE": database.database, "PGUSER": database.username}
    container = os.getenv("IPS_POSTGRES_CONTAINER")
    if container:
        command = ["docker", "exec", "-i", "-e", "PGDATABASE", "-e", "PGUSER", container, *args]
    else:
        env.update(
            PGHOST=str(database.query.get("host") or database.host or "localhost"),
            PGPORT=str(database.port or 5432),
            PGPASSWORD=database.password or "",
        )
        command = list(args)
    subprocess.run(command, env=env, timeout=30, check=True, **streams)


def test_m3_upgrade_and_backup_restore(databases, tmp_path):
    source, restored = databases(), databases()
    migrate(source, M3_REVISION)
    with api_process(source, tmp_path / "m3.log") as client:
        expected = seed_demo(str(client.base_url), WEB_ORIGIN, PASSWORD)
    migrate(source)
    [head] = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini"))).get_heads()
    with (tmp_path / "demo.dump").open("w+b") as backup:
        pg_tool(source, "pg_dump", "--format=custom", "--no-owner", stdout=backup)
        backup.seek(0)
        pg_tool(
            restored,
            "pg_restore",
            f"--dbname={restored.database}",
            "--no-owner",
            "--exit-on-error",
            "--single-transaction",
            stdin=backup,
        )
    engine = create_engine(restored)
    try:
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == head
    finally:
        engine.dispose()
    with api_process(restored, tmp_path / "restored.log") as client:
        for company in expected:
            staff_login(client, company["email"])
            widgets = client.get("/api/v1/widgets").json()["items"]
            articles = client.get("/api/v1/knowledge/articles").json()["items"]
            assert [item["public_key"] for item in widgets] == [company["public_key"]]
            assert {item["visibility"] for item in articles} == {"public", "internal"}
            assert all(item["title"].startswith(company["company"]) for item in articles)
