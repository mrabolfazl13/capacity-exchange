"""`/api/v1/auth` — register, login, refresh rotation, logout and profile (§3)."""
from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from app.core.deps import CurrentUser, SessionDep
from app.core.routing import TransactionalRoute
from app.schemas.identity import (
    LoginInput,
    LogoutInput,
    RefreshInput,
    RegisterInput,
    UserOut,
    UserPatch,
)
from app.services import auth as svc

router = APIRouter(route_class=TransactionalRoute, prefix="/auth", tags=["auth"])


def _dump_user(payload: dict) -> dict:
    return UserOut.model_validate(payload).model_dump(mode="json")


@router.post("/register", status_code=201)
async def register(body: RegisterInput, request: Request, session: SessionDep) -> JSONResponse:
    data = await svc.register(session, request.app.state.settings, body, request)
    return JSONResponse(status_code=201, content={
        **{k: v for k, v in data.items() if k != "user"},
        "user": _dump_user(data["user"]),
    })


@router.post("/login")
async def login(body: LoginInput, request: Request, session: SessionDep) -> dict:
    return await svc.login(session, request.app.state.settings, body.email, body.password, request)


@router.post("/refresh")
async def refresh(body: RefreshInput, request: Request, session: SessionDep) -> dict:
    return await svc.refresh(session, request.app.state.settings, body.refresh_token, request)


@router.post("/logout", status_code=204)
async def logout(body: LogoutInput, request: Request, session: SessionDep,
                 user: CurrentUser) -> JSONResponse:
    await svc.logout(session, body.refresh_token, user_id=user.id)
    return JSONResponse(status_code=204, content=None)


@router.get("/me")
async def me(request: Request, session: SessionDep, user: CurrentUser) -> dict:
    return _dump_user(await svc.user_dto(session, user))


@router.patch("/me")
async def patch_me(body: UserPatch, request: Request, session: SessionDep,
                   user: CurrentUser) -> dict:
    for name, value in body.model_dump(exclude_none=True).items():
        setattr(user, name, value)
    await session.flush()
    return _dump_user(await svc.user_dto(session, user))
