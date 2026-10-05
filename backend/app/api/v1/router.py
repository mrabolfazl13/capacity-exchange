"""`/api/v1` router registry.

Endpoints land here as their domain services become real; a module is only wired once it
serves actual data, so the surface never advertises a route that would return a stub (§12:
no fake actions).
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    auth,
    bookings,
    capacity,
    commerce,
    marketplace,
    matching,
    organizations,
)

router = APIRouter()
router.include_router(auth.router)
router.include_router(organizations.router)
router.include_router(capacity.catalog_router)
router.include_router(capacity.router)
router.include_router(marketplace.router)
router.include_router(matching.router)
router.include_router(bookings.router)
router.include_router(commerce.router)
