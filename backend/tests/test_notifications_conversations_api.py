"""§5.7/§8 messaging surface: notification bell, SSE stream, conversation threads and receipts.

The stream is asserted through a real HTTP request that overlaps another request writing an
event, so the live tail is proven on the path clients use rather than against the generator.
"""
from __future__ import annotations

import asyncio
import json
import uuid

from tests.test_auth_api import auth, register
from tests.test_booking_api import live_offer
from tests.test_capacity_api import make_provider
from tests.test_commerce_api import book_offer, confirm, intent

QUESTION = "Is the projector usable for a session that runs past 17:00?"
ANSWER = "Yes, the room is staffed until 19:00 and the projector stays set up."
FOLLOWUP = "Perfect, we will bring the whole team."


async def notifications_of(client, tokens, **params) -> dict:
    resp = await client.get("/notifications", headers=auth(tokens), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def kinds(payload: dict) -> list[str]:
    return [item["kind"] for item in payload["items"]]


async def conversations_of(client, tokens, **params) -> dict:
    resp = await client.get("/conversations", headers=auth(tokens), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def messages_of(client, tokens, conversation_id: str, **params) -> dict:
    resp = await client.get(f"/conversations/{conversation_id}/messages",
                            headers=auth(tokens), params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def send(client, tokens, conversation_id: str, body: str) -> tuple[int, dict]:
    resp = await client.post(f"/conversations/{conversation_id}/messages",
                             headers=auth(tokens), json={"body": body})
    return resp.status_code, resp.json()


async def start_thread(client, tokens, **changes) -> tuple[int, dict]:
    body = {"kind": "booking", "initial_body": QUESTION}
    body.update(changes)
    resp = await client.post("/conversations", headers=auth(tokens), json=body)
    return resp.status_code, resp.json()


async def paid_booking(client, provider, buyer, offer_id: str) -> dict:
    booking = await book_offer(client, buyer, offer_id)
    payment = await intent(client, buyer, booking["order_id"])
    code, settled = await confirm(client, buyer, payment["id"])
    assert code == 200, settled
    return booking


async def test_booking_and_payment_events_reach_the_right_side(client):
    provider = await make_provider(client, "nt.prov@example.test")
    offer = await live_offer(client, provider, quantity=2)
    buyer = await register(client, "nt.buyer@example.test")
    stranger = await register(client, "nt.stranger@example.test")

    booking = await book_offer(client, buyer, offer["id"])
    mine = await notifications_of(client, buyer, unread="true")
    assert {"items", "total", "limit", "offset"} == set(mine)
    assert mine["total"] == 1 and kinds(mine) == ["order.created"]
    row = mine["items"][0]
    assert row["user_id"] == buyer["user"]["id"] and row["org_id"] is None
    assert row["channel"] == "in_app" and row["read_at"] is None and row["sent_at"] is None
    assert row["title"].startswith("Order CX-")
    assert row["data"] == {"order_id": booking["order_id"], "booking_id": booking["id"]}
    assert row["created_at"].endswith("Z")
    assert (await notifications_of(client, buyer))["total"] == 1
    assert (await notifications_of(client, provider))["items"] == []
    assert (await notifications_of(client, stranger))["items"] == []
    assert (await client.get("/notifications")).status_code == 401

    await paid_booking(client, provider, buyer, offer["id"])
    as_provider = await notifications_of(client, provider, unread="true")
    assert kinds(as_provider) == ["booking.paid"]
    assert as_provider["items"][0]["org_id"] == provider["user"]["active_org_id"]
    buyer_notes = kinds(await notifications_of(client, buyer))
    assert buyer_notes[0] == "order.paid"  # newest first
    assert buyer_notes.count("order.created") == 2  # one per booking

    read = await client.post(f"/notifications/{as_provider['items'][0]['id']}/read",
                             headers=auth(provider))
    assert read.status_code == 200, read.text
    assert read.json()["read_at"].endswith("Z")
    assert (await notifications_of(client, provider, unread="true"))["items"] == []
    again = await client.post(f"/notifications/{as_provider['items'][0]['id']}/read",
                              headers=auth(provider))
    assert again.status_code == 200 and again.json()["read_at"] == read.json()["read_at"]

    foreign = await client.post(f"/notifications/{row['id']}/read", headers=auth(stranger))
    assert foreign.status_code == 404
    assert (await client.post(f"/notifications/{uuid.uuid4()}/read", headers=auth(buyer))
            ).status_code == 404

    cancelled = await client.post(f"/bookings/{booking['id']}/cancel", headers=auth(buyer),
                                  json={"reason": "plans changed"})
    assert cancelled.status_code == 200, cancelled.text
    after = await notifications_of(client, buyer)
    assert "booking.cancelled" in kinds(after)

    bulk = await client.post("/notifications/read-all", headers=auth(buyer))
    assert bulk.status_code == 200, bulk.text
    assert bulk.json()["ok"] is True and bulk.json()["updated"] >= 1
    assert (await notifications_of(client, buyer, unread="true"))["items"] == []
    assert (await client.post("/notifications/read-all", headers=auth(buyer))
            ).json()["updated"] == 0


async def test_the_stream_replays_the_backlog_then_the_live_event(client):
    provider = await make_provider(client, "nt2.prov@example.test")
    offer = await live_offer(client, provider, quantity=2)
    buyer = await register(client, "nt2.buyer@example.test")
    booking = await paid_booking(client, provider, buyer, offer["id"])

    no_token = await client.get("/notifications/stream")
    assert no_token.status_code == 401
    assert (await client.get("/notifications/stream",
                             params={"access_token": "not-a-jwt"})).status_code == 401

    tail = await client.post("/conversations", headers=auth(buyer), json={
        "kind": "booking", "ref_id": booking["id"], "initial_body": QUESTION})
    assert tail.status_code == 201, tail.text

    stream = asyncio.create_task(client.get("/notifications/stream", params={
        "access_token": buyer["access_token"], "seconds": 1.5, "poll": 0.2, "limit": 25}))
    await asyncio.sleep(0.1)
    # Written while the stream is open: it must arrive as a live event, not in the snapshot.
    cancel_task = asyncio.create_task(
        client.post(f"/bookings/{booking['id']}/cancel", headers=auth(buyer),
                    json={"reason": "changed plans"}))
    response = await stream
    assert (await cancel_task).status_code == 200

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in response.text.splitlines()
              if line.startswith("data: ")]
    assert [e["kind"] for e in events] == ["order.created", "order.paid", "booking.cancelled"]
    assert all(e["created_at"].endswith("Z") for e in events)
    assert events[0]["data"]["booking_id"] == booking["id"]
    assert ": stream window closed" in response.text

    # The provider's stream only ever carries the provider's own rows.
    provider_events = await client.get("/notifications/stream", params={
        "access_token": provider["access_token"], "seconds": 0.4, "poll": 0.2})
    parsed = [json.loads(line[6:]) for line in provider_events.text.splitlines()
              if line.startswith("data: ")]
    assert {"booking.paid", "message.created"} == {e["kind"] for e in parsed}
    assert all(e["user_id"] == provider["user"]["id"] for e in parsed)


async def test_a_booking_thread_is_shared_reused_and_read_by_both_sides(client):
    provider = await make_provider(client, "cv.prov@example.test")
    offer = await live_offer(client, provider, quantity=2)
    buyer = await register(client, "cv.buyer@example.test")
    stranger = await register(client, "cv.stranger@example.test")
    booking = await paid_booking(client, provider, buyer, offer["id"])

    missing_ref = await start_thread(client, buyer, ref_id=None)
    assert missing_ref[0] == 400
    ghost = await start_thread(client, buyer, ref_id=str(uuid.uuid4()))
    assert ghost[0] == 404
    nonsense = await start_thread(client, buyer, ref_id=booking["id"], kind="carrier")
    assert nonsense[0] == 400
    assert sorted(nonsense[1]["error"]["details"]["allowed"]) == ["booking", "offer", "other"]
    assert (await client.post("/conversations", json={"kind": "booking"})).status_code == 401

    stranger_thread = await client.post("/conversations", headers=auth(stranger), json={
        "kind": "booking", "ref_id": booking["id"], "initial_body": QUESTION})
    assert stranger_thread.status_code == 403

    code, thread = await start_thread(client, buyer, ref_id=booking["id"])
    assert code == 201, thread
    assert thread["kind"] == "booking" and thread["ref_id"] == booking["id"]
    assert thread["customer_id"] == buyer["user"]["id"]
    assert thread["provider_org_id"] == provider["user"]["active_org_id"]
    assert thread["org_id"] == provider["user"]["active_org_id"]
    assert thread["status"] == "open" and thread["peer_name"] == "Room Owner Org"
    assert thread["created_at"].endswith("Z") and thread["last_message_at"].endswith("Z")

    repeat = await start_thread(client, buyer, ref_id=booking["id"])
    assert repeat[0] == 200 and repeat[1]["id"] == thread["id"]
    from_other_side = await start_thread(client, provider, ref_id=booking["id"])
    assert from_other_side[0] == 200 and from_other_side[1]["id"] == thread["id"]
    assert from_other_side[1]["peer_name"] == "Test User"

    listed = await conversations_of(client, buyer)
    assert listed["total"] == 1 and listed["items"][0]["id"] == thread["id"]
    assert listed["items"][0]["last_message_body"] == QUESTION
    assert listed["items"][0]["unread_count"] == 0
    workspace = await conversations_of(client, provider, provider="true")
    assert workspace["total"] == 1 and workspace["items"][0]["unread_count"] == 1
    assert workspace["items"][0]["peer_name"] == "Test User"
    assert (await conversations_of(client, stranger))["items"] == []
    assert (await conversations_of(client, provider))["items"] == []
    assert (await client.get(f"/conversations/{thread['id']}/messages",
                             headers=auth(stranger))).status_code == 403
    assert (await client.get(f"/conversations/{uuid.uuid4()}/messages",
                             headers=auth(buyer))).status_code == 404
    bad_status = await client.get("/conversations", headers=auth(buyer),
                                  params={"status": "muted"})
    assert bad_status.status_code == 400
    assert bad_status.json()["error"]["details"]["allowed"] == ["open", "closed", "archived"]

    thread_messages = await messages_of(client, provider, thread["id"])
    assert thread_messages["total"] == 1
    first = thread_messages["items"][0]
    assert first["body"] == QUESTION and first["sender_id"] == buyer["user"]["id"]
    assert first["sender_name"] == "Test User" and first["is_system"] is False
    assert first["created_at"].endswith("Z")
    staff_id = provider["user"]["id"]
    assert first["read_receipts"][staff_id].endswith("Z")
    assert buyer["user"]["id"] not in first["read_receipts"]
    assert (await conversations_of(client, provider, provider="true"))["items"][0][
        "unread_count"] == 0

    code, reply = await send(client, provider, thread["id"], ANSWER)
    assert code == 201, reply
    assert reply["conversation_id"] == thread["id"] and reply["sender_name"] == "Room Owner"
    assert reply["read_receipts"] == {}
    # The provider's answer waits as an unread badge until the customer opens the thread.
    assert (await conversations_of(client, buyer))["items"][0]["unread_count"] == 1
    thread_messages = await messages_of(client, buyer, thread["id"])
    assert thread_messages["total"] == 2
    assert [m["body"] for m in thread_messages["items"]] == [QUESTION, ANSWER]
    assert (await conversations_of(client, buyer))["items"][0]["unread_count"] == 0

    def bell(payload: dict, kind: str) -> list[str]:
        return [i["body"] for i in payload["items"] if i["kind"] == kind]

    assert bell(await notifications_of(client, buyer), "message.created") == [ANSWER]
    # Only the customer's question reaches the org; the provider's reply is not echoed back.
    assert bell(await notifications_of(client, provider), "message.created") == [QUESTION]

    blank = await client.post(f"/conversations/{thread['id']}/messages", headers=auth(buyer),
                              json={"body": "   "})
    assert blank.status_code == 422
    assert (await client.post(f"/conversations/{thread['id']}/messages", headers=auth(buyer),
                              json={"body": "x" * 4001})).status_code == 422


async def test_listing_enquiries_and_thread_status_follow_the_documented_rules(client):
    provider = await make_provider(client, "cv2.prov@example.test")
    offer = await live_offer(client, provider, quantity=2)
    buyer = await register(client, "cv2.buyer@example.test")
    staff = await register(client, "cv2.staff@example.test", provider=True, name="Other Owner")

    code, thread = await start_thread(client, buyer, kind="offer", ref_id=offer["id"])
    assert code == 201, thread
    assert thread["kind"] == "offer" and thread["ref_id"] == offer["id"]
    assert thread["provider_org_id"] == provider["user"]["active_org_id"]
    assert (await start_thread(client, buyer, kind="offer", ref_id=offer["id"]))[0] == 200
    # The repeat carried the same opening text but wrote nothing: reuse does not duplicate.
    assert (await messages_of(client, buyer, thread["id"]))["total"] == 1

    own_listing = await start_thread(client, provider, kind="offer", ref_id=offer["id"])
    assert own_listing[0] == 400
    assert (await start_thread(client, buyer, kind="offer", ref_id=str(uuid.uuid4())))[0] == 404

    direct = await client.post("/conversations", headers=auth(staff), json={
        "kind": "other", "org_id": provider["user"]["active_org_id"],
        "initial_body": "We need a pallet-sized room twice a month."})
    assert direct.status_code == 201, direct.text
    assert direct.json()["kind"] == "other"
    assert (await client.post("/conversations", headers=auth(buyer),
                              json={"kind": "other"})).status_code == 400
    second_direct = await client.post("/conversations", headers=auth(staff), json={
        "kind": "other", "org_id": provider["user"]["active_org_id"]})
    assert second_direct.status_code == 200
    assert second_direct.json()["id"] == direct.json()["id"]
    assert (await messages_of(client, staff, direct.json()["id"]))["total"] == 1

    code, closed = await client_post(client, buyer, thread["id"], "closed")
    assert code == 200 and closed["status"] == "closed"
    assert (await send(client, buyer, thread["id"], FOLLOWUP))[0] == 400
    code, reopened = await client_post(client, provider, thread["id"], "open")
    assert code == 200 and reopened["status"] == "open"
    assert (await send(client, buyer, thread["id"], FOLLOWUP))[0] == 201
    assert (await conversations_of(client, buyer, status="open"))["total"] == 1

    same = await client_post(client, buyer, thread["id"], "open")
    assert same[0] == 409
    code, archived = await client_post(client, buyer, thread["id"], "archived")
    assert code == 200 and archived["status"] == "archived"
    assert (await client_post(client, buyer, thread["id"], "open"))[0] == 400
    assert (await client_post(client, buyer, thread["id"], "muted"))[0] == 422
    assert (await client_post(client, staff, thread["id"], "open"))[0] == 403


async def client_post(client, tokens, conversation_id: str, status: str) -> tuple[int, dict]:
    resp = await client.post(f"/conversations/{conversation_id}/status", headers=auth(tokens),
                             json={"status": status})
    return resp.status_code, resp.json()
