# Security Specification

## Authentication
- secure password hashing
- short-lived access tokens
- refresh token rotation where appropriate
- session revocation

## Authorization
RBAC plus resource ownership/tenant isolation.

## API
- validation
- rate limiting
- safe error responses
- CORS configuration
- request size limits
- audit logging

## Data
- secrets only through environment/configuration
- no credentials in source
- secure file handling
- tenant isolation
- minimal sensitive data retention

## Business Security
Detect suspicious booking/payment patterns, excessive cancellation, abnormal account behavior, and duplicated requests.

Security controls must be tested, not merely documented.
