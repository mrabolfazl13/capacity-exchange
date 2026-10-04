"""Model registry — importing this module binds every mapper (alembic `target_metadata` source)."""
from __future__ import annotations

from app.models.base import Base
from app.models.booking import (
    ACTIVE_BOOKING_STATUSES,
    BOOKING_PAYMENT_STATUSES,
    BOOKING_STATUSES,
    BOOKING_TRANSITIONS,
    TERMINAL_BOOKING_STATUSES,
    Booking,
    BookingStatusEvent,
    bookings_payment_enum,
    bookings_status_enum,
)
from app.models.capacity import (
    AvailabilityOverride,
    CapacityCategory,
    CapacityDefinition,
    CapacityDefinitionException,
    CapacityResource,
    RecurringAvailability,
)
from app.models.commerce import (
    Commission,
    Fulfillment,
    Order,
    Payment,
    PaymentEvent,
    Refund,
)
from app.models.crosscut import (
    AuditLog,
    Conversation,
    CouponRedemption,
    Dispute,
    IdempotencyKey,
    Message,
    Notification,
    Promotion,
    Review,
)
from app.models.identity import (
    OrgStaff,
    Organization,
    Session,
    Tenant,
    User,
    UserRole,
)
from app.models.marketplace import Demand, Match, Offer

__all__ = [
    "ACTIVE_BOOKING_STATUSES",
    "AuditLog",
    "AvailabilityOverride",
    "Base",
    "BOOKING_PAYMENT_STATUSES",
    "BOOKING_STATUSES",
    "BOOKING_TRANSITIONS",
    "Booking",
    "BookingStatusEvent",
    "CapacityCategory",
    "CapacityDefinition",
    "CapacityDefinitionException",
    "CapacityResource",
    "Commission",
    "Conversation",
    "CouponRedemption",
    "Demand",
    "Dispute",
    "Fulfillment",
    "IdempotencyKey",
    "Match",
    "Message",
    "Notification",
    "Order",
    "OrgStaff",
    "Organization",
    "Payment",
    "PaymentEvent",
    "Promotion",
    "RecurringAvailability",
    "Refund",
    "Review",
    "Session",
    "TERMINAL_BOOKING_STATUSES",
    "Tenant",
    "User",
    "UserRole",
    "bookings_payment_enum",
    "bookings_status_enum",
]
