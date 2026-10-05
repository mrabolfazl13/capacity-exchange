"""§8 dashboard aggregates — their own module because they mix booking and order DTOs.

Importing them from `commerce.py` would create a cycle: `schemas.booking` depends on
`schemas.commerce`, so anything referencing `BookingOut` must sit above both.
"""
from __future__ import annotations

from app.schemas.booking import BookingOut
from app.schemas.common import ORMModel, WireUUID
from app.schemas.commerce import OrderOut


class TopOfferRow(ORMModel):
    offer_id: WireUUID
    title: str
    bookings: int
    revenue_cents: int


class CustomerDashboard(ORMModel):
    active_bookings: list[BookingOut] = []
    spend_cents: int = 0
    spend_currency: str = "USD"
    unread_notifications: int = 0
    recent_orders: list[OrderOut] = []


class ProviderDashboard(ORMModel):
    utilization_pct: float = 0.0
    bookings_by_status: dict[str, int] = {}
    revenue_cents: int = 0
    revenue_currency: str = "USD"
    upcoming_bookings: list[BookingOut] = []
    top_offers: list[TopOfferRow] = []


class AdminDashboard(ORMModel):
    users_total: int = 0
    orgs_total: int = 0
    offers_total: int = 0
    bookings_total: int = 0
    gmv_cents: int = 0
    currency: str = "USD"
    commission_cents: int = 0
    open_disputes: int = 0
