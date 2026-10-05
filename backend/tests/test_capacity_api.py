"""§5.2 capacity surface: wizard path, tenant isolation, expansion, overrides, idempotency."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from tests.test_auth_api import auth, register

DAY = (date.today() + timedelta(days=3)).isoformat()
DOW = date.fromisoformat(DAY).weekday()


async def make_provider(client, email: str, name: str = "Room Owner") -> dict:
    return await register(client, email, provider=True, name=name)


async def make_resource(client, tokens: dict, *, name: str = "North Room",
                       category_key: str = "meeting_room", headers: dict | None = None,
                       definitions: list | None = None) -> dict:
    cats = await client.get("/catalog/categories", headers=headers or auth(tokens))
    category_id = next(c["id"] for c in cats.json()["items"] if c["key"] == category_key)
    body = {
        "name": name, "category_id": category_id, "capacity_mode": "scheduled",
        "address": {"line1": "1 Test St", "city": "Tehran", "country": "IR"},
        "timezone": "UTC", "attributes": {"capacity_persons": 8},
    }
    if definitions:
        body["definitions"] = definitions
    resp = await client.post("/capacities", json=body, headers=auth(tokens))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def add_rule(client, tokens: dict, resource_id: str, definition_id: str,
                   *, quantity: int = 2) -> dict:
    resp = await client.post(f"/capacities/{resource_id}/availability", headers=auth(tokens),
                             json={"definition_id": definition_id, "dow": DOW,
                                   "start_time": "09:00:00", "end_time": "18:00:00",
                                   "quantity": quantity, "valid_from": DAY,
                                   "valid_until": (date.fromisoformat(DAY)
                                                  + timedelta(days=30)).isoformat()})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_category_registry_is_seeded_and_enveloped(client):
    tokens = await register(client, "cat1@example.test")
    resp = await client.get("/catalog/categories", headers=auth(tokens))
    assert resp.status_code == 200
    body = resp.json()
    assert {"items", "total", "limit", "offset"} <= set(body)
    keys = {c["key"] for c in body["items"]}
    assert {"meeting_room", "warehouse", "truck_return", "production_slot"} <= keys
    assert body["total"] == len(body["items"]) == 12


async def test_categories_are_readable_without_org_membership(client):
    tokens = await register(client, "cat2@example.test")
    resp = await client.get("/catalog/categories", headers=auth(tokens))
    assert resp.status_code == 200
    anonymous = await client.get("/catalog/categories")
    assert anonymous.status_code == 401


async def test_provider_wizard_resource_definition_and_free_windows(client):
    tokens = await make_provider(client, "prov.wiz@example.test")
    resource = await make_resource(client, tokens, name="Wizard Room")
    assert resource["status"] == "draft"
    assert resource["org_id"] == tokens["user"]["active_org_id"]
    assert resource["address"]["city"] == "Tehran"

    definition = (await client.post(f"/capacities/{resource['id']}/definitions",
                                    headers=auth(tokens), json={
                                        "name": "Hour slot", "unit_label": "hour",
                                        "min_quantity": 1, "max_quantity": 2,
                                        "slot_duration_minutes": 60})).json()
    assert definition["max_quantity"] == 2

    await add_rule(client, tokens, resource["id"], definition["id"], quantity=2)

    free = await client.get("/availability/free", headers=auth(tokens), params={
        "definition_id": definition["id"], "from": f"{DAY}T10:00:00Z", "to": f"{DAY}T12:00:00Z"})
    assert free.status_code == 200, free.text
    items = free.json()["items"]
    assert len(items) == 1
    assert items[0]["free_quantity"] == 2
    assert items[0]["window_start"] == f"{DAY}T10:00:00Z"
    assert items[0]["window_end"] == f"{DAY}T12:00:00Z"


async def test_resource_can_be_created_with_nested_definitions(client):
    tokens = await make_provider(client, "prov.nested@example.test")
    resource = await make_resource(client, tokens, name="Nested Room", definitions=[
        {"name": "Bay", "unit_label": "bay", "min_quantity": 1, "max_quantity": 4}])
    assert len(resource["definitions"]) == 1
    assert resource["definitions"][0]["max_quantity"] == 4
    listed = await client.get(f"/capacities/{resource['id']}/definitions", headers=auth(tokens))
    assert listed.json()["total"] == 1


async def test_availability_day_plan_reports_planned_and_free(client):
    tokens = await make_provider(client, "prov.plan@example.test")
    resource = await make_resource(client, tokens, name="Plan Room")
    definition = (await client.post(f"/capacities/{resource['id']}/definitions",
                                    headers=auth(tokens), json={
                                        "name": "Day", "unit_label": "day",
                                        "min_quantity": 1, "max_quantity": 3})).json()
    await add_rule(client, tokens, resource["id"], definition["id"], quantity=3)
    to_day = (date.fromisoformat(DAY) + timedelta(days=8)).isoformat()

    resp = await client.get(f"/capacities/{resource['id']}/availability", headers=auth(tokens),
                            params={"definition_id": definition["id"], "from": DAY, "to": to_day})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    days = [d for d in body["days"] if d["date"] == DAY]
    assert len(days) == 1
    assert days[0]["total_quantity"] == 3
    assert days[0]["free_quantity"] == 3
    assert days[0]["booked_quantity"] == 0
    assert len(body["recurring"]) == 1
    assert body["recurring"][0]["start_time"].startswith("09:00")
    assert len(body["days"]) == 9


async def test_closed_override_removes_the_day_and_deleting_it_restores_capacity(client):
    tokens = await make_provider(client, "prov.ovr@example.test")
    resource = await make_resource(client, tokens, name="Override Room")
    definition = (await client.post(f"/capacities/{resource['id']}/definitions",
                                    headers=auth(tokens), json={
                                        "name": "Slot", "unit_label": "hour",
                                        "min_quantity": 1, "max_quantity": 2})).json()
    await add_rule(client, tokens, resource["id"], definition["id"])
    params = {"definition_id": definition["id"], "from": f"{DAY}T10:00:00Z",
              "to": f"{DAY}T12:00:00Z"}

    created = await client.post(f"/capacities/{resource['id']}/availability/overrides",
                                headers=auth(tokens), json={
                                    "definition_id": definition["id"], "override_date": DAY,
                                    "kind": "closed", "reason": "Annual maintenance"})
    assert created.status_code == 201, created.text
    override_id = created.json()["id"]

    closed = await client.get("/availability/free", headers=auth(tokens), params=params)
    assert closed.json()["items"] == []

    removed = await client.delete(f"/availability/{override_id}", headers=auth(tokens))
    assert removed.status_code == 204
    back = await client.get("/availability/free", headers=auth(tokens), params=params)
    assert back.json()["items"][0]["free_quantity"] == 2


async def test_partial_day_override_only_shrinks_the_touched_slice(client):
    tokens = await make_provider(client, "prov.part@example.test")
    resource = await make_resource(client, tokens, name="Partial Room")
    definition = (await client.post(f"/capacities/{resource['id']}/definitions",
                                    headers=auth(tokens), json={
                                        "name": "Slot", "unit_label": "hour",
                                        "min_quantity": 1, "max_quantity": 5})).json()
    await add_rule(client, tokens, resource["id"], definition["id"], quantity=5)

    await client.post(f"/capacities/{resource['id']}/availability/overrides",
                      headers=auth(tokens), json={
                          "definition_id": definition["id"], "override_date": DAY,
                          "kind": "closed", "start_time": "12:00:00", "end_time": "14:00:00"})
    free = await client.get("/availability/free", headers=auth(tokens), params={
        "definition_id": definition["id"], "from": f"{DAY}T09:00:00Z", "to": f"{DAY}T18:00:00Z"})
    windows = [(w["window_start"][11:16], w["window_end"][11:16], w["free_quantity"])
               for w in free.json()["items"]]
    assert windows == [("09:00", "12:00", 5), ("14:00", "18:00", 5)]


async def test_other_org_cannot_read_or_mutate_the_capacity(client):
    owner = await make_provider(client, "prov.owner@example.test")
    intruder = await make_provider(client, "prov.intruder@example.test")
    resource = await make_resource(client, owner, name="Private Room")
    definition = (await client.post(f"/capacities/{resource['id']}/definitions",
                                    headers=auth(owner), json={
                                        "name": "Slot", "unit_label": "hour",
                                        "min_quantity": 1, "max_quantity": 1})).json()

    got = await client.get(f"/capacities/{resource['id']}", headers=auth(intruder))
    assert got.status_code == 403
    assert got.json()["error"]["code"] == "ownership_required"

    patched = await client.patch(f"/capacities/{resource['id']}", headers=auth(intruder),
                                 json={"name": "hijacked"})
    assert patched.status_code == 403

    raised = await client.patch(f"/definitions/{definition['id']}", headers=auth(intruder),
                                json={"max_quantity": 999})
    assert raised.status_code == 403
    still = await client.get(f"/capacities/{resource['id']}/definitions", headers=auth(owner))
    assert still.json()["items"][0]["max_quantity"] == 1


async def test_free_windows_are_visible_to_a_customer(client):
    owner = await make_provider(client, "prov.pub@example.test")
    buyer = await register(client, "buyer.free@example.test")
    resource = await make_resource(client, owner, name="Open Room")
    definition = (await client.post(f"/capacities/{resource['id']}/definitions",
                                    headers=auth(owner), json={
                                        "name": "Slot", "unit_label": "hour",
                                        "min_quantity": 1, "max_quantity": 2})).json()
    await add_rule(client, owner, resource["id"], definition["id"])

    resp = await client.get("/availability/free", headers=auth(buyer), params={
        "definition_id": definition["id"], "from": f"{DAY}T10:00:00Z", "to": f"{DAY}T11:00:00Z"})
    assert resp.status_code == 200
    assert resp.json()["items"][0]["free_quantity"] == 2
    hidden = await client.get(f"/capacities/{resource['id']}/availability",
                              headers=auth(buyer), params={"from": DAY, "to": DAY})
    assert hidden.status_code == 403


async def test_replay_of_the_same_idempotency_key_returns_the_same_resource(client):
    tokens = await make_provider(client, "prov.idem@example.test")
    cats = await client.get("/catalog/categories", headers=auth(tokens))
    category_id = next(c["id"] for c in cats.json()["items"] if c["key"] == "warehouse")
    body = {"name": "Idempotent Bay", "category_id": category_id, "capacity_mode": "quantity",
            "timezone": "UTC"}
    first = await client.post("/capacities", json=body, headers={**auth(tokens),
                                                                "Idempotency-Key": "k-1"})
    second = await client.post("/capacities", json=body, headers={**auth(tokens),
                                                                 "Idempotency-Key": "k-1"})
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]

    changed = await client.post("/capacities", json={**body, "name": "Different"},
                                headers={**auth(tokens), "Idempotency-Key": "k-1"})
    assert changed.status_code == 409


async def test_validation_and_range_errors_use_the_error_envelope(client):
    tokens = await make_provider(client, "prov.val@example.test")
    cats = await client.get("/catalog/categories", headers=auth(tokens))
    category_id = cats.json()["items"][0]["id"]

    short_name = await client.post("/capacities", headers=auth(tokens), json={
        "name": "x", "category_id": category_id})
    assert short_name.status_code == 422
    assert short_name.json()["error"]["code"] == "validation_error"

    bad_category = await client.post("/capacities", headers=auth(tokens), json={
        "name": "Valid name", "category_id": "11111111-1111-1111-1111-111111111111"})
    assert bad_category.status_code == 400
    assert bad_category.json()["error"]["code"] == "validation_error"

    unknown = await client.get("/availability/free", headers=auth(tokens), params={
        "definition_id": "11111111-1111-1111-1111-111111111111",
        "from": f"{DAY}T10:00:00Z", "to": f"{DAY}T12:00:00Z"})
    assert unknown.status_code == 404

    resource = await make_resource(client, tokens, name="Range Room")
    definition = (await client.post(f"/capacities/{resource['id']}/definitions",
                                    headers=auth(tokens), json={
                                        "name": "Slot", "unit_label": "hour",
                                        "min_quantity": 1, "max_quantity": 1})).json()
    params = {"definition_id": definition["id"]}

    inverted = await client.get("/availability/free", headers=auth(tokens), params={
        **params, "from": f"{DAY}T12:00:00Z", "to": f"{DAY}T10:00:00Z"})
    assert inverted.status_code == 422
    assert inverted.json()["error"]["code"] == "range_mismatch"

    malformed = await client.get("/availability/free", headers=auth(tokens), params={
        **params, "from": "yesterday", "to": f"{DAY}T10:00:00Z"})
    assert malformed.status_code == 422

    huge = await client.get("/availability/free", headers=auth(tokens), params={
        **params, "from": f"{DAY}T00:00:00Z",
        "to": (datetime.fromisoformat(DAY) + timedelta(days=400)).date().isoformat() + "T00:00:00Z"})
    assert huge.status_code == 422
    assert huge.json()["error"]["code"] == "range_mismatch"

    # An override that closes the whole day must not be combinable with a partial one.
    no_quantity = await client.post(f"/capacities/{resource['id']}/availability/overrides",
                                   headers=auth(tokens), json={
                                       "definition_id": definition["id"], "override_date": DAY,
                                       "kind": "reduced"})
    assert no_quantity.status_code == 400


async def test_deleting_a_capacity_with_offers_is_refused(client, db):
    from sqlalchemy import delete

    from app.models.marketplace import Offer

    tokens = await make_provider(client, "prov.del@example.test")
    resource = await make_resource(client, tokens, name="Doomed Room")
    definition = (await client.post(f"/capacities/{resource['id']}/definitions",
                                    headers=auth(tokens), json={
                                        "name": "Slot", "unit_label": "hour",
                                        "min_quantity": 1, "max_quantity": 1})).json()
    db.add(Offer(org_id=tokens["user"]["active_org_id"], definition_id=definition["id"],
                 resource_id=resource["id"], title="Keeps the row",
                 description="Blocks the delete until closed.", status="published"))
    await db.commit()

    blocked = await client.delete(f"/capacities/{resource['id']}", headers=auth(tokens))
    assert blocked.status_code == 409
    assert "offer" in blocked.json()["error"]["message"]

    await db.execute(delete(Offer).where(Offer.resource_id == resource["id"]))
    await db.commit()
    ok = await client.delete(f"/capacities/{resource['id']}", headers=auth(tokens))
    assert ok.status_code == 204


async def test_patch_capacity_status_and_definition_limits(client):
    tokens = await make_provider(client, "prov.patch@example.test")
    resource = await make_resource(client, tokens, name="Patch Room")
    patched = await client.patch(f"/capacities/{resource['id']}", headers=auth(tokens),
                                 json={"status": "active", "timezone": "Europe/Berlin"})
    assert patched.status_code == 200
    assert patched.json()["status"] == "active"
    assert patched.json()["timezone"] == "Europe/Berlin"

    definition = (await client.post(f"/capacities/{resource['id']}/definitions",
                                    headers=auth(tokens), json={
                                        "name": "Slot", "unit_label": "hour",
                                        "min_quantity": 2, "max_quantity": 4})).json()
    bad = await client.patch(f"/definitions/{definition['id']}", headers=auth(tokens),
                             json={"max_quantity": 1})
    assert bad.status_code == 400
    good = await client.patch(f"/definitions/{definition['id']}", headers=auth(tokens),
                              json={"max_quantity": 2, "is_active": False})
    assert good.status_code == 200
    assert good.json()["max_quantity"] == 2 and good.json()["is_active"] is False


async def test_duplicate_availability_rule_is_a_conflict(client):
    tokens = await make_provider(client, "prov.dup@example.test")
    resource = await make_resource(client, tokens, name="Dup Room")
    definition = (await client.post(f"/capacities/{resource['id']}/definitions",
                                    headers=auth(tokens), json={
                                        "name": "Slot", "unit_label": "hour",
                                        "min_quantity": 1, "max_quantity": 1})).json()
    await add_rule(client, tokens, resource["id"], definition["id"])
    again = await client.post(f"/capacities/{resource['id']}/availability",
                              headers=auth(tokens), json={
                                  "definition_id": definition["id"], "dow": DOW,
                                  "start_time": "09:00:00", "end_time": "17:00:00",
                                  "quantity": 1, "valid_from": DAY})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "conflict"
