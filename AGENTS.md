# Agent Operating Rules

## Ownership
- Architecture: contracts, boundaries, ADRs
- Backend: backend/ and backend tests
- Desktop: desktop/
- Mobile: mobile/
- AI/Data: ai/
- QA/Security: tests/, security checks
- Performance/Release: infrastructure/, docker/, release assets

## Shared Files
Changes to these require synchronization:
- database migrations
- API schemas
- authentication
- booking state machine
- shared DTOs
- root configuration

## Workflow
1. Read MASTER_PROMPT.md.
2. Read relevant docs.
3. Read CURRENT_STATE.md.
4. Inspect existing implementation.
5. Claim a task in CURRENT_STATE.md.
6. Implement.
7. Test.
8. Update docs.
9. Record blockers and next sync point.

## Conflict Avoidance
Never overwrite another agent's work blindly. Rebase/reconcile conceptually by inspecting current files first. Prefer additive, focused changes.

## Handoff
Every completed task must state:
- files changed
- APIs/contracts changed
- migrations added
- tests run
- known issues
- next dependent task
