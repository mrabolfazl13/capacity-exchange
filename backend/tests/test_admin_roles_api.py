"""§4 role desk: the platform grants and revokes global roles, and both take effect immediately.

A role that only changes behaviour when the access token happens to be re-issued is a revocation
that does not happen for half an hour, so the tests drive one token through grant and revoke
without ever logging in again.
"""
from __future__ import annotations

from tests.test_auth_api import auth, login, register
from tests.test_capacity_api import make_provider
from tests.test_reviews_disputes_api import staff

CUSTOMER = "rl.buyer@example.test"


async def listed(client, admin, path, **params) -> dict:
    resp = await client.get(path, headers=auth(admin), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def grant(client, admin, user_id: str, role: str) -> tuple[int, dict]:
    resp = await client.post(f"/admin/users/{user_id}/roles", headers=auth(admin),
                             json={"role": role})
    return resp.status_code, resp.json()


async def revoke(client, admin, user_id: str, role: str) -> tuple[int, dict | None]:
    resp = await client.delete(f"/admin/users/{user_id}/roles/{role}", headers=auth(admin))
    return resp.status_code, resp.json() if resp.content else None


async def test_a_grant_is_written_by_the_platform_and_shown_on_the_account(client, db):
    admin = await staff(client, db, "rl.admin@example.test", "platform_admin")
    support = await staff(client, db, "rl.support@example.test", "support")
    target = await register(client, CUSTOMER)
    user_id = target["user"]["id"]

    code, grant_row = await grant(client, admin, user_id, "org_admin")
    assert code == 201, grant_row
    assert grant_row["user_id"] == user_id and grant_row["role"] == "org_admin"
    assert grant_row["granted_by"] == admin["user"]["id"]
    assert grant_row["created_at"].endswith("Z")

    mine = await listed(client, admin, f"/admin/users/{user_id}/roles")
    assert mine["total"] == 1 and mine["items"][0]["role"] == "org_admin"

    queued = await listed(client, admin, "/admin/users", role="org_admin")
    assert [row["email"] for row in queued["items"]] == [CUSTOMER]
    assert queued["items"][0]["roles"] == ["customer", "org_admin"], "customer stays implicit"

    again, clash = await grant(client, admin, user_id, "org_admin")
    assert again == 409 and clash["error"]["message"] == "org_admin is already granted to this user"

    bad, body = await grant(client, admin, user_id, "customer")
    assert bad == 400 and body["error"]["code"] == "validation_error", body
    assert body["error"]["message"] == "Role cannot be granted"
    assert body["error"]["details"]["allowed"] == \
        ["platform_admin", "support", "org_admin", "provider"]

    odd, typed = await grant(client, admin, user_id, "superuser")
    assert odd == 422 and "Input should be 'platform_admin'" in str(typed["error"]["details"])

    missing, gone = await grant(client, admin, "7a1f9c3e-0000-4000-8000-000000000000", "support")
    assert missing == 404 and gone["error"]["message"] == "User not found"

    provider = await make_provider(client, "rl.prov@example.test")
    for tokens, path, method in ((provider, f"/admin/users/{user_id}/roles", "post"),
                                 (support, f"/admin/users/{user_id}/roles", "post")):
        refused = await client.request(method, path, headers=auth(tokens), json={"role": "support"})
        assert refused.status_code == 403
        assert refused.json()["error"]["message"] == "Platform role required"

    anonymous = await client.post(f"/admin/users/{user_id}/roles", json={"role": "support"})
    assert anonymous.status_code == 401

    trail = await listed(client, admin, "/admin/audit-logs", entity_id=user_id)
    granted = next(row for row in trail["items"] if row["action"] == "admin.role_granted")
    assert granted["after"] == {"role": "org_admin"}
    assert granted["actor_user_id"] == admin["user"]["id"]
    assert granted["entity_type"] == "user"


async def test_a_role_works_on_the_next_request_and_ends_on_the_next_one(client, db):
    admin = await staff(client, db, "rl.live.admin@example.test", "platform_admin")
    buyer = await register(client, "rl.live.buyer@example.test")
    user_id = buyer["user"]["id"]

    refused = await client.get("/admin/disputes", headers=auth(buyer))
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "role_required"

    assert (await grant(client, admin, user_id, "support"))[0] == 201
    settled = await client.get("/admin/disputes", headers=auth(buyer))
    assert settled.status_code == 200, "the grant reaches the very next request on the old token"
    assert settled.json()["items"] == []

    still_not_admin = await client.get("/admin/users", headers=auth(buyer))
    assert still_not_admin.status_code == 403
    assert still_not_admin.json()["error"]["message"] == "Platform role required"

    seen = await client.get("/auth/me", headers=auth(buyer))
    assert sorted(seen.json()["roles"]) == ["customer", "support"]

    code, _ = await revoke(client, admin, user_id, "support")
    assert code == 204
    after = await client.get("/admin/disputes", headers=auth(buyer))
    assert after.status_code == 403, "and so does the revocation, on the same token"
    assert (await listed(client, admin, f"/admin/users/{user_id}/roles"))["total"] == 0

    seen = await client.get("/auth/me", headers=auth(buyer))
    assert seen.json()["roles"] == ["customer"], "the console is the only place a role comes from"


async def test_the_platform_always_keeps_one_administrator(client, db):
    admin = await staff(client, db, "rl.only.admin@example.test", "platform_admin")
    second = await register(client, "rl.only.second@example.test")
    admin_id = admin["user"]["id"]

    last, kept = await revoke(client, admin, admin_id, "platform_admin")
    assert last == 409 and kept["error"]["message"] == \
        "The platform must keep at least one administrator"
    assert (await listed(client, admin, f"/admin/users/{admin_id}/roles"))["total"] == 1

    assert (await grant(client, admin, second["user"]["id"], "platform_admin"))[0] == 201
    code, _ = await revoke(client, admin, admin_id, "platform_admin")
    assert code == 204
    locked = await client.get("/admin/users", headers=auth(admin))
    assert locked.status_code == 403, "the revocation closes the console on the next request"

    survivor = await login(client, "rl.only.second@example.test")
    rows = await listed(client, survivor, "/admin/users", role="platform_admin")
    assert [row["email"] for row in rows["items"]] == ["rl.only.second@example.test"]

    never, missing = await revoke(client, survivor, admin_id, "support")
    assert never == 404 and missing["error"]["message"] == "support is not granted to this user"
    odd, rejected = await revoke(client, survivor, admin_id, "customer")
    assert odd == 400 and rejected["error"]["message"] == "Unknown role"

    trail = await listed(client, survivor, "/admin/audit-logs", action="admin.role_revoked")
    assert [row["before"] for row in trail["items"]] == [{"role": "platform_admin"}]
    assert [row["after"] for row in trail["items"]] == [None]
