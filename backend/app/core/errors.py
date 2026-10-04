"""AppError hierarchy + exception handlers emitting the CONTRACTS §2 envelope.

Envelope: {"error": {"code", "message", "details", "request_id"}}.
request_id comes from the request-id middleware (`request.state.request_id`).
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse


class AppError(Exception):
    """Base for all controlled failures. http_status + machine code + human message."""

    http_status: int = 400
    code: str = "unprocessable"

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None,
                 code: str | None = None, http_status: int | None = None) -> None:
        self.message = message or self.__doc__ or "error"
        self.details = details or {}
        if code:
            self.code = code
        if http_status:
            self.http_status = http_status
        super().__init__(message)


# --- 400 ---
class ValidationFailed(AppError):
    http_status, code = 400, "validation_error"

class InvalidStateTransition(AppError):
    http_status, code = 400, "invalid_state_transition"

class Unprocessable(AppError):
    http_status, code = 400, "unprocessable"

# --- 401 ---
class Unauthorized(AppError):
    http_status, code = 401, "unauthorized"

class TokenExpired(AppError):
    http_status, code = 401, "token_expired"

class TokenInvalid(AppError):
    http_status, code = 401, "token_invalid"

class SessionRevoked(AppError):
    http_status, code = 401, "session_revoked"

# --- 403 ---
class Forbidden(AppError):
    http_status, code = 403, "forbidden"

class NotMember(AppError):
    http_status, code = 403, "not_member"

class OwnershipRequired(AppError):
    http_status, code = 403, "ownership_required"

class RoleRequired(AppError):
    http_status, code = 403, "role_required"

class TenantMismatch(AppError):
    http_status, code = 403, "tenant_mismatch"

# --- 404 ---
class NotFound(AppError):
    http_status, code = 404, "not_found"

# --- 409 (taxonomy row §2: conflict & friends, incl. invalid_credentials) ---
class Conflict(AppError):
    http_status, code = 409, "conflict"

class NoAvailability(AppError):
    http_status, code = 409, "no_availability"

class CapacityExceeded(AppError):
    http_status, code = 409, "capacity_exceeded"

class HoldExpired(AppError):
    http_status, code = 409, "hold_expired"

class AlreadyRated(AppError):
    http_status, code = 409, "already_rated"

class DuplicateRequest(AppError):
    http_status, code = 409, "duplicate_request"

class InvalidCredentials(AppError):
    """Per CONTRACTS §2 taxonomy, bad credentials are 409 invalid_credentials."""
    http_status, code = 409, "invalid_credentials"

# --- 410 ---
class Gone(AppError):
    http_status, code = 410, "gone"

# --- 422 ---
class ApiValidationError(AppError):
    http_status, code = 422, "validation_error"

class RangeMismatch(AppError):
    http_status, code = 422, "range_mismatch"

# --- 429 ---
class RateLimited(AppError):
    http_status, code = 429, "rate_limited"

    def __init__(self, message: str = "Too many requests", *, retry_after_seconds: int = 1,
                 details: dict[str, Any] | None = None) -> None:
        d = {"retry_after_seconds": retry_after_seconds}
        if details:
            d.update(details)
        super().__init__(message, details=d)

# --- 500 / 503 ---
class InternalError(AppError):
    http_status, code = 500, "internal_error"

class DependencyUnavailable(AppError):
    http_status, code = 503, "dependency_unavailable"


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def error_body(code: str, message: str, details: dict[str, Any], request_id: str) -> dict:
    return {"error": {"code": code, "message": message, "details": details, "request_id": request_id}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        headers = {}
        if exc.http_status == 429:
            headers["Retry-After"] = str(exc.details.get("retry_after_seconds", 1))
        return JSONResponse(
            status_code=exc.http_status,
            content=error_body(exc.code, exc.message, exc.details, _request_id(request)),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        field_errors = [
            {"field": ".".join(str(p) for p in err["loc"][1:]) or "body", "message": err["msg"]}
            for err in exc.errors()
        ]
        # 422 => validation_error per §2 (FastAPI raises 422; body-field issues from clients are also 400-capable)
        return JSONResponse(
            status_code=422,
            content=error_body("validation_error", "Request validation failed",
                               {"field_errors": field_errors}, _request_id(request)),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        mapping = {401: "unauthorized", 403: "forbidden", 404: "not_found",
                   405: "unprocessable", 409: "conflict", 410: "gone",
                   422: "validation_error", 429: "rate_limited", 503: "dependency_unavailable"}
        code = mapping.get(exc.status_code, "unprocessable" if exc.status_code < 500 else "internal_error")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, str(exc.detail), {}, _request_id(request)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError) -> JSONResponse:
        # Surface unique/FK violations as conflicts, never leaking SQL internals to clients.
        return JSONResponse(
            status_code=409,
            content=error_body("conflict", "Resource violates a uniqueness or reference constraint.",
                               {}, _request_id(request)),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        import logging
        logging.getLogger("capacityexchange.errors").exception(
            "unhandled error request_id=%s", _request_id(request))
        return JSONResponse(
            status_code=500,
            content=error_body("internal_error", "Internal server error", {}, _request_id(request)),
        )
