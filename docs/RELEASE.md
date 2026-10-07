# Release Runbook

This is the procedure a release actually follows, not a wish list. Every command here has been
run or is the command the pipeline runs; anything that has not been proven is called out at the
bottom.

## Version policy

One version per release, and the git tag must equal it. It is written in nine places because
each platform demands its own:

| Where | File |
| --- | --- |
| Backend `/health` and logs | `backend/app/core/config.py` (`app_version`) |
| Backend package | `backend/pyproject.toml` |
| AI package + `capacity_ai.__version__` | `ai/pyproject.toml`, `ai/capacity_ai/__init__.py` |
| Desktop web bundle | `desktop/package.json` |
| Tauri app (installer metadata) | `desktop/src-tauri/tauri.conf.json`, `Cargo.toml`, `Cargo.lock` |
| Android `versionName`/`versionCode` | `mobile/pubspec.yaml` (`1.0.0+1`) |

`docs/CONTRACTS.md` §12 is the API-shape authority; a version bump never changes a contract.

## Cutting a release

1. Everything that can run locally is green (see `docs/TESTING_STRATEGY.md` for the commands):
   `ai` 31, `backend` 97, `desktop` 42, `mobile` 13, and the live trio journey 39 / race PASS /
   matrix 33.
2. Configure the repository once:
   - **variable** `API_BASE_URL` — the URL the shipped clients call. `prepare` refuses an unset
     value, and refuses `http://` to any host the Android network-security config does not
     exempt (`10.0.2.2`, `localhost`, `127.0.0.1`).
   - **secrets** `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`,
     `ANDROID_KEY_PASSWORD` — the signing material. The keystore is gitignored on purpose; the
     job names whichever of the four is missing instead of producing a debug-signed "release".
3. Tag and push:
   ```
   git tag -a v1.0.0 -m "Capacity Exchange 1.0.0"
   git push origin v1.0.0
   ```
   `.github/workflows/release.yml` then builds the Windows NSIS installer, the Android release
   APK and the Play app bundle, verifies each file is a real artifact of the expected size, and
   attaches them to a GitHub release for that tag. Nothing in this workflow deploys to a server:
   pushing the tag is the human decision, and it is the only trigger.

A `workflow_dispatch` run produces the same artifacts without publishing a release, which is how
a build is checked before a tag is spent.

## Verifying the artifacts by hand

An installer that launches is not evidence; the quality bar requires the action to reach the API
and persist real state. For each new build:

- **Windows** — install on a clean machine (the bundle is `currentUser`, so no admin), launch,
  sign in against the release API, publish a resource through the capacity wizard, search for it
  as a customer, hold → confirm → pay → complete, and leave a review. Then check the same booking
  in `/bookings?provider=true` and in the customer's order list: both must agree.
- **Android** — install the APK on a device, sign in, and confirm a search returns the offer the
  desktop account published. A build that cannot reach its API is the failure mode the
  INTERNET-permission and `--dart-define` fixes exist to prevent.
- **App bundle** — upload to Play internal testing; `bundletool` output is not a device test.

## Deploying the backend

From the repo root, on a Docker host:

```
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml up -d
```

- The `backend` container runs `alembic upgrade head` at startup; `worker`/`beat` set
  `SKIP_MIGRATIONS=1` so exactly one container migrates and they cannot race.
- `SECRET_KEY` and `WEBHOOK_SECRET` have **no** production defaults — the compose file requires
  them from the deploy environment and nothing in the repository supplies a value.
- Postgres and Redis publish no host ports in the prod overlay; the backend is reached through a
  TLS-terminating proxy pointed at `backend:8000`.
- `GET /health` answers `{"status": "ok", "db": true, "redis": true, "version": "1.0.0"}` — the
  readiness probe for a proxy, and the first call to make after a deploy.
- Logs are plain text (`timestamp level logger message`) from `logging.basicConfig`. Error
  responses carry a `request_id` (§2) that appears in the log line, which is how a user-facing
  error is traced to a server record. There is no JSON log pipeline.

### Backup and restore

```
docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml \
  exec postgres pg_dump -U capacity -Fc capacity > backup.dump
docker compose -f ... exec -T postgres pg_restore -U capacity -d capacity --clean --if-exists < backup.dump
```

Take one before every migration on a live database.

### Rollback

Redeploy the previous image tag. For a schema change, **restore the pre-migration dump** rather
than running `alembic downgrade`: the current history is one migration (`0001_core_schema`) whose
downgrade drops the entire schema, so it is a destroy operation, not a rollback. A release that
changes the schema ships a forward-only migration plus a backup.

## Not yet proven

- **The compose topology has never been booted.** There is no Docker on the development machine,
  so both files are validated by reading and by structure, not by a running stack. The first
  deploy is therefore a supervised event, not a routine one.
- **Redis-backed behaviour in production** — CI runs Redis for the live gate, but the local
  development box never has, so the Redis rate-limit window and Celery job paths have only run
  in CI.
- **No signed Windows package yet** — the NSIS installer is unsigned, so SmartScreen will warn on
  first run. Code signing needs a certificate that is not configured; adding it is a change to
  the `tauri-windows` job, not to the app.
- **No automated deploy anywhere.** The pipeline builds and publishes artifacts; taking them live
  is a human action on a host.
