"""`/api/v1/conversations` — the booking and listing threads (§5.7/§8)."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request
from starlette.responses import JSONResponse

from app.api.v1.deps import ActorDep, PageDep, envelope
from app.core.deps import SessionDep
from app.core.errors import ValidationFailed
from app.core.routing import TransactionalRoute
from app.models.crosscut import CONVERSATION_STATUSES
from app.schemas.commerce import (
    ConversationCreateInput,
    ConversationOut,
    ConversationStatusInput,
    MessageInput,
    MessageOut,
)
from app.services import conversations as svc

router = APIRouter(route_class=TransactionalRoute, prefix="/conversations", tags=["conversations"])


def _payload(row, *, view: str) -> dict:
    return ConversationOut.model_validate(svc.row_to_dict(row, view=view)).model_dump(mode="json")


def _view(row, actor) -> str:
    return "provider" if row.Conversation.provider_org_id in actor.org_ids else "customer"


@router.get("")
async def list_conversations(session: SessionDep, actor: ActorDep, page: PageDep,
                             provider: bool = False,
                             status: Annotated[str | None, Query(max_length=16)] = None) -> dict:
    """Inbox view: the caller's own threads, or the org's when `provider=true`."""
    if status is not None and status not in CONVERSATION_STATUSES:
        raise ValidationFailed("Unknown conversation status",
                               details={"allowed": list(CONVERSATION_STATUSES)})
    items, total = await svc.list_for_actor(
        session, user_id=actor.user_id, org_ids=actor.org_ids,
        is_platform_admin=actor.is_platform_admin, provider=provider, status=status,
        limit=page.limit, offset=page.offset)
    return envelope(items, int(total), page)


@router.post("", status_code=201)
async def start_conversation(body: ConversationCreateInput, session: SessionDep,
                             actor: ActorDep) -> JSONResponse:
    """Open the thread for a booking or listing; a repeat returns the thread it found."""
    conversation, created = await svc.open_conversation(
        session, user_id=actor.user_id, org_ids=actor.org_ids,
        is_platform_admin=actor.is_platform_admin, kind=body.kind, ref_id=body.ref_id,
        org_id=body.org_id, initial_body=body.initial_body)
    row = await svc.load_row(session, conversation.id)
    return JSONResponse(status_code=201 if created else 200,
                        content=_payload(row, view=_view(row, actor)))


@router.get("/{conversation_id}/messages")
async def list_messages(conversation_id: uuid.UUID, session: SessionDep, actor: ActorDep,
                        page: PageDep) -> dict:
    """Oldest first; reading the thread is what marks the other side's messages as seen."""
    row = await svc.get_for_actor(session, conversation_id, user_id=actor.user_id,
                                 org_ids=actor.org_ids,
                                 is_platform_admin=actor.is_platform_admin)
    items, total = await svc.list_messages(session, row.Conversation, user_id=actor.user_id,
                                           limit=page.limit, offset=page.offset)
    return envelope(items, int(total), page)


@router.post("/{conversation_id}/messages", status_code=201)
async def send_message(conversation_id: uuid.UUID, body: MessageInput, session: SessionDep,
                       actor: ActorDep) -> dict:
    row = await svc.get_for_actor(session, conversation_id, user_id=actor.user_id,
                                 org_ids=actor.org_ids,
                                 is_platform_admin=actor.is_platform_admin)
    message = await svc.post_message(session, row.Conversation, sender_id=actor.user_id,
                                     body=body.body)
    return MessageOut.model_validate(svc.message_to_dict(message,
                                                         sender_name=actor.full_name)
                                     ).model_dump(mode="json")


@router.post("/{conversation_id}/status")
async def set_status(conversation_id: uuid.UUID, body: ConversationStatusInput,
                     request: Request, session: SessionDep, actor: ActorDep) -> dict:
    """Either side can close or reopen; archiving is terminal."""
    row = await svc.get_for_actor(session, conversation_id, user_id=actor.user_id,
                                  org_ids=actor.org_ids,
                                  is_platform_admin=actor.is_platform_admin)
    await svc.set_status(session, row.Conversation, to_status=body.status,
                         actor_user_id=actor.user_id, request=request)
    return _payload(await svc.load_row(session, conversation_id), view=_view(row, actor))
