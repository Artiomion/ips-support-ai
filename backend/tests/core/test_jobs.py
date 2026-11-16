import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from ips.core.config import Settings
from ips.core.jobs import drain_once, enqueue


@pytest.fixture
def engine():
    engine = create_engine(Settings().database_url, connect_args={"connect_timeout": 2})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError:
        if os.getenv("IPS_REQUIRE_DATABASE") == "1":
            pytest.fail("PostgreSQL is required for this test run")
        pytest.skip("PostgreSQL unavailable")
    yield engine
    engine.dispose()


def test_job_idempotency(engine):
    tenant = uuid4()
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO organizations (id, name) VALUES (:id, :name)"),
            {"id": tenant, "name": "test"},
        )
        first = enqueue(conn, tenant, "test", {"value": 1}, f"test:{tenant}")
        second = enqueue(conn, tenant, "test", {"value": 1}, f"test:{tenant}")
        assert first == second
    seen = []
    assert drain_once(engine, {"test": lambda payload: seen.append(payload)}) is True
    assert seen == [{"value": 1}]
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM jobs WHERE tenant_id = :tenant"), {"tenant": tenant})
        conn.execute(text("DELETE FROM organizations WHERE id = :tenant"), {"tenant": tenant})
