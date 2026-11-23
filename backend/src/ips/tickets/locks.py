"""Locks shared by capacity-sensitive ticket operations."""

from sqlalchemy import text


def lock_handoff_memberships(db, tenant_id):
    db.execute(
        text("SELECT id FROM memberships WHERE tenant_id=:tenant ORDER BY id FOR UPDATE"),
        {"tenant": tenant_id},
    ).all()
