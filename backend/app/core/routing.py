"""Route plumbing shared by every API router (CONTRACTS §2).

FastAPI closes a dependency's `yield` teardown *after* the response has already gone to
the client, so a session that commits there makes a write visible only *after* the 201
that announced it. Measured on a live server: a freshly registered user's next request
got "User is inactive or no longer exists" 8 times in 14. It also means a commit that
fails — a deferred booking constraint, a full disk — is reported as success, because the
error surfaces after the status line was already sent.

`TransactionalRoute` commits when the endpoint has produced its response and before that
response leaves the handler, so read-your-write is a property of the API rather than a
race, and a failed commit becomes the 5xx it is.
"""
from __future__ import annotations

from fastapi import Request, Response
from fastapi.routing import APIRoute


class TransactionalRoute(APIRoute):
    """Runs the session commit inside the request, not in dependency teardown."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            response = await original(request)
            # Only when the endpoint actually handed a session back: reads and writes
            # alike are done with it, and an exception here never reaches this line.
            session = getattr(request.state, "db_session", None)
            if session is not None and session.in_transaction():
                await session.commit()
            return response

        return handler
