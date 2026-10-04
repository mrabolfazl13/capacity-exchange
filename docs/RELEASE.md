# Release Specification

## Development
Docker Compose should provide:
- PostgreSQL
- Redis
- backend
- Celery worker
- Celery scheduler where needed

## Desktop
Build Windows Tauri package.

Verify:
- clean installation
- application launch
- backend connectivity
- authentication
- marketplace
- booking

## Mobile
Flutter:
- Android debug/release build configuration
- iOS project configuration
- environment-based API URL
- secure configuration

## Production
Provide:
- environment variable documentation
- migration commands
- backup guidance
- health endpoints
- structured logging
- basic observability
- rollback guidance
