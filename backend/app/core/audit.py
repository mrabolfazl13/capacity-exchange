"""Audit writer used by all domain services (CONTRACTS §5.7 audit_logs).

Usage: `await record_audit(session, request, action="user.login", entity_type="user",
entity_id=user.id, after={...})` — the row is added to the caller's transaction;
never commits by itself, so audits stay atomic with the mutation they describe.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.crosscut import AuditLog


def _request_context(request: Request | None) -> dict[str, Any]:
    ctx: dict[str, Any] = {"actor_user_id": None, "actor_org_id": None,
                           "ip": None, "user_agent": None, "request_id": None}
    if request is None:
        return ctx
    ctx["actor_user_id"] = getattr(request.state, "current_user_id", None)
    ctx["actor_org_id"] = getattr(request.state, "current_org_id", None)
    ctx["ip"] = request.client.host if request.client else None
    ctx["user_agent"] = request.headers.get("user-agent")
    rid = getattr(request.state, "request_id", None)
    ctx["request_id"] = str(rid)[:64] if rid else None
    return ctx


async def record_audit(
    session: AsyncSession,
    request: Request | None,
    *,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
    before: Any = None,
    after: Any = None,
    actor_user_id: uuid.UUID | None = None,
    actor_org_id: uuid.UUID | None = None,
) -> AuditLog:
    ctx = _request_context(request)
    row = AuditLog(
        actor_user_id=actor_user_id or ctx["actor_user_id"],
        actor_org_id=actor_org_id or ctx["actor_org_id"],
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before,
        after=after,
        ip=ctx["ip"],
        user_agent=ctx["user_agent"],
        request_id=ctx["request_id"],
    )
    session.add(row)
    return row
