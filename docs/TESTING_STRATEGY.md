# Testing Strategy

## Backend
- unit tests
- service tests
- repository tests
- API integration tests
- migration tests

## Booking
Mandatory:
- two users booking same unit simultaneously
- hold expiration
- duplicate requests
- retry after timeout
- cancellation
- invalid state transitions

## Frontend
- component tests
- form validation
- API state handling
- navigation
- critical flows

## Flutter
- unit
- widget
- integration tests for discovery and booking

## E2E
At minimum:
register → provider setup → publish capacity → customer search → book → confirm → provider fulfillment → review.

## Security
Test unauthorized access, tenant crossing, role escalation, invalid ownership and malformed requests.

## Performance
Measure marketplace search, availability queries, booking confirmation and dashboard queries.
