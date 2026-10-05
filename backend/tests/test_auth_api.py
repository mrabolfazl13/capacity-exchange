"""§3 auth contract tests: token shapes, rotation, replay revocation, /auth/me."""
from __future__ import annotations

import pytest


async def register(client, email: str, *, provider: bool = False, name: str = "Test User"):
    body = {"email": email, "password": "Sup3rSecret!", "full_name": name}
    if provider:
        body["account_type"] = "provider"
        body["organization"] = {"name": f"{name} Org", "currency": "USD", "timezone": "UTC"}
    resp = await client.post("/auth/register", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def login(client, email: str, password: str = "Sup3rSecret!"):
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


def auth(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_register_customer_returns_token_pair_and_dto(client):
    data = await register(client, "cust1@example.test")
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == pytest.approx(30 * 60, abs=5)
    user = data["user"]
    assert user["email"] == "cust1@example.test"
    assert user["roles"] == ["customer"]  # §4: customer is implicit, nothing else granted
    assert user["active_org_id"] is None
    assert "password" not in str(data)


async def test_register_provider_creates_org_and_grants_roles(client):
    data = await register(client, "prov1@example.test", provider=True, name="North Room")
    user = data["user"]
    assert set(user["roles"]) >= {"provider", "org_admin"}
    assert user["active_org_id"]
    org = await client.get(f"/organizations/{user['active_org_id']}", headers=auth(data))
    assert org.status_code == 200
    assert org.json()["slug"] == "north-room-org"


async def test_duplicate_email_is_409_conflict(client):
    await register(client, "dupe@example.test")
    resp = await client.post("/auth/register", json={
        "email": "Dupe@Example.test", "password": "Sup3rSecret!", "full_name": "Other"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"


async def test_login_with_wrong_password_does_not_reveal_which_part(client):
    await register(client, "login1@example.test")
    resp = await client.post("/auth/login", json={
        "email": "login1@example.test", "password": "WrongPassword1!"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "invalid_credentials"
    resp2 = await client.post("/auth/login", json={
        "email": "ghost@example.test", "password": "WrongPassword1!"})
    assert resp2.status_code == 409
    assert resp2.json()["error"]["message"] == resp.json()["error"]["message"]


async def test_refresh_rotates_and_replay_revokes_the_family(client):
    tokens = await register(client, "rot1@example.test")
    first = tokens["refresh_token"]
    rotated = await client.post("/auth/refresh", json={"refresh_token": first})
    assert rotated.status_code == 200, rotated.text
    new_refresh = rotated.json()["refresh_token"]
    assert new_refresh != first

    replay = await client.post("/auth/refresh", json={"refresh_token": first})
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "session_revoked"

    after = await client.post("/auth/refresh", json={"refresh_token": new_refresh})
    assert after.status_code == 401


async def test_access_token_is_rejected_by_the_refresh_endpoint(client):
    tokens = await register(client, "typ1@example.test")
    resp = await client.post("/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert resp.status_code == 401


async def test_me_requires_and_uses_the_bearer_token(client):
    tokens = await register(client, "me1@example.test")
    anonymous = await client.get("/auth/me")
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "unauthorized"

    me = await client.get("/auth/me", headers=auth(tokens))
    assert me.status_code == 200
    assert me.json()["email"] == "me1@example.test"

    patched = await client.patch("/auth/me", headers=auth(tokens),
                                 json={"full_name": "Renamed User"})
    assert patched.status_code == 200 and patched.json()["full_name"] == "Renamed User"


async def test_logout_revokes_the_session(client):
    tokens = await register(client, "out1@example.test")
    out = await client.post("/auth/logout", headers=auth(tokens),
                            json={"refresh_token": tokens["refresh_token"]})
    assert out.status_code == 204
    replay = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "session_revoked"


async def test_expired_access_token_is_reported_as_token_expired(client, settings):
    from app.core.security import create_access_token

    tokens = await register(client, "exp1@example.test")
    stale, _ = create_access_token(settings.model_copy(update={"access_token_ttl_min": -1}),
                                   user_id=tokens["user"]["id"], org_id=None, roles=["customer"])
    resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {stale}"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "token_expired"
