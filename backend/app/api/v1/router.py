"""`/api/v1` router registry.

Endpoints land here as their domain services become real; a module is only wired once it
serves actual data, so the surface never advertises a route that would return a stub (§12:
no fake actions).
"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    assistant,
    auth,
    bookings,
    capacity,
    commerce,
    conversations,
    dashboard,
    disputes,
    marketplace,
    matching,
    notifications,
    organizations,
    reviews,
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
router.include_router(reviews.router)
router.include_router(disputes.router)
router.include_router(conversations.router)
router.include_router(notifications.router)
router.include_router(dashboard.router)
router.include_router(admin.router)
router.include_router(assistant.router)
