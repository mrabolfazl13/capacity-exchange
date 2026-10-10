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
   `ai` 31, `backend` 403, `desktop` 51, `mobile` 13, the live trio journey 39 / race PASS /
   matrix 33, and the performance gate (`bash infrastructure/scripts/perf_gate.sh`) with all eight
   p95s inside budget. CI adds one proof this box cannot produce at all: `desktop native` compiles
   the Tauri shell in release mode (Rust release linking is OOM-killed locally), so a broken native
   layer fails here rather than at the installer step of a release run.
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

## 1.0.0 — what this tag actually is (2026-10-11)

Tag `v1.0.0` → commit `ef58eec258d49424a235737195d2cf2dce735a19`.
Release: https://github.com/mrabolfazl13/capacity-exchange/releases/tag/v1.0.0 (no assets, by design).

Steps 1 and 3 ran; step 2 did not, and that decides what this release contains.

- Green on the tagged commit in CI — run `38092227388`, conclusion `success`: `backend` (migrations
  + pytest on postgres:16), `desktop` (web build + vitest), `ai`, `live gate` (journey + race +
  security matrix over HTTP), `mobile` (analyze + test + `flutter build apk --debug`, uploaded as
  `mobile-android-apk-debug`, a build proof and not a release artifact).
- Green on the release box, where CI has no PostgreSQL 16 with a 40,000-booking corpus: the
  performance gate, re-run end to end twice on the release candidate with 8 of 8 p95 inside budget.
  The tagged commit changes documentation only — no route, service or query it measures.
- **`release.yml` stops at `prepare`** — run `38092532378`, job `check release inputs`, step
  `API base URL is configured and safe`, annotation:
  `repository variable API_BASE_URL is not set; a release cannot be built against no API`.
  Every downstream job — `tauri windows installer (nsis)`, `android release (apk + aab)`,
  `tauri linux`, `desktop web bundle`, `attach artifacts to the tag` — reported `skipped`, so no
  partial or debug-signed file reached the release. The release therefore carries **source plus
  notes and zero assets**: no Windows installer, no signed APK, no app bundle. Those five settings
  (`API_BASE_URL` plus the four `ANDROID_KEYSTORE_*` secrets) are the entire difference between
  this section and a binary release; setting them and re-running the workflow on this tag is all
  that remains.
- The published release notes state the same thing, listing both what ships and what does not.

### After the tag: the full build sweep (2026-10-11)

`v1.0.0` stays where it was cut — it points at the commit CI actually validated, and moving a
published tag means rewriting history. What the sweep found was a coverage hole worth fixing forward:

- **No workflow had ever compiled the native desktop layer.** `desktop (web build + vitest)` builds
  the bundle only, and the sole job that could compile Rust (`tauri linux`) was gated behind
  `prepare`, so on a repository without `API_BASE_URL` it was skipped along with the jobs that
  genuinely need the host. A broken `Cargo.toml`, an invalid capability file or a
  `tauri.conf.json` the CLI no longer parses therefore went green — on every push, and on the tag.
- `desktop native (tauri linux compile check)` now runs on every push: it installs the system
  dependencies, builds in release mode with `--no-bundle`, and asserts the output is a linked ELF
  executable over 200 KB rather than merely a non-empty file. Green on `c8462b8` (run `38093116039`,
  six jobs) and again after the assertion was tightened (`cf19021`, run `38093466337`).
- The release-side job no longer `needs: prepare` and no longer receives `VITE_API_BASE_URL`: a
  compile check that ships nothing has no business appearing to validate a configured client. A
  manual `workflow_dispatch` proves the shape — run `38093154524`: `check release inputs` `failure`,
  the four artifact jobs `skipped`, `tauri linux (compile check…)` **success**.
- What this does *not* buy: the app is still never installed or launched in CI, so a native
  capability that only misbehaves at runtime remains outside every layer, and the two installer
  artifacts are still blocked on the same five settings.

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
