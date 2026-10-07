"""A write must be committed before the response that announces it (§2).

FastAPI runs a dependency's `yield` teardown after the response has already gone out, so
a session that commits there lets `POST /auth/register` answer 201 while the account is
still invisible. Measured against a live server before this was fixed: 8 of 14 freshly
registered users were told on their next request that they did not exist.

The probe asks the question at the ASGI boundary — while the response starts, can a
second connection already see the row? — so it fails on the old ordering rather than
passing by accident.
"""
from __future__ import annotations

import uuid

import httpx
from sqlalchemy import select

from app.models.identity import User

PASSWORD = "Demo1234!"


def _body(email: str, slug: str) -> dict:
    return {
        "email": email,
        "password": PASSWORD,
        "full_name": "Commit Probe",
        "roles": ["provider"],
        "organization": {"name": f"Probe Org {slug}", "slug": slug,
                         "currency": "USD", "timezone": "UTC"},
    }


async def _row_is_committed(app, email: str) -> bool:
    """Ask a different connection — the one the client's next request would get."""
    async with app.state.sessionmaker() as session:
        found = (await session.execute(select(User.id).where(User.email == email))).first()
    return found is not None


async def test_a_201_reaches_the_client_only_after_its_row_is_visible(app):
    slug = uuid.uuid4().hex[:10]
    email = f"commit.{slug}@example.test"
    seen: list[bool] = []

    async def spy(scope, receive, send):
        async def send_with_probe(message):
            if message["type"] == "http.response.start" and scope["path"].endswith("/auth/register"):
                seen.append(await _row_is_committed(app, email))
            await send(message)

        await app(scope, receive, send_with_probe)

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=spy),
                                     base_url="http://test/api/v1") as c:
            resp = await c.post("/auth/register", json=_body(email, slug))

    assert resp.status_code == 201, resp.text
    assert seen == [True], "the response went out before the transaction committed"


async def test_a_client_can_read_back_what_it_just_created(app):
    """The ordering the E2E journey depends on: register, then GET /auth/me."""
    slug = uuid.uuid4().hex[:10]
    email = f"read.{slug}@example.test"

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test/api/v1") as c:
            reg = await c.post("/auth/register", json=_body(email, slug))
            assert reg.status_code == 201, reg.text
            token = reg.json()["access_token"]
            me = await c.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert me.status_code == 200, me.text
    assert me.json()["email"] == email
