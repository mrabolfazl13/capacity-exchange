# Domain Model

## Identity
Tenant, Organization, User, Role, Permission.

## Capacity
CapacityResource = physical/logical asset.
CapacityDefinition = sellable definition of what can be used.
CapacityUnit = unit or quantity.
Availability = when capacity can be consumed.

## Commerce
Offer = published sellable capacity.
Demand = customer need.
Match = compatibility between demand and offer.
Booking = reserved capacity.
Order = commercial transaction.
Payment = payment attempt/transaction.
Commission = platform revenue.

## Fulfillment
Fulfillment, Review, Dispute.

## Cross Cutting
Notification, Conversation, Message, AuditLog, Promotion, Coupon.

All entities must have appropriate identifiers, timestamps, ownership/tenant boundaries, and lifecycle state where applicable.
