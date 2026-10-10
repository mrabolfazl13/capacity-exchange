"""Selector-loop API launcher for the performance gate (Windows dev boxes only need it).

Why this exists instead of `python -m uvicorn app.main:app`:

  uvicorn's asyncio loop factory hard-codes `ProactorEventLoop` on win32 unless it is spawning
  subprocesses (`--reload` or `--workers > 1`), and psycopg3 refuses to run async on a proactor
  loop. A plain `uvicorn app.main:app` on this platform therefore answers **every** database
  request with `500 internal_error` — measured, not theorised: the first perf run failed at
  `POST /auth/login` with exactly that, and the traceback in the server log is
  `psycopg.InterfaceError: Psycopg cannot use the 'ProactorEventLoop'`.

  The reload mode hides the problem (which is why `dev_backend.sh` works), so the fix cannot be
  "always pass --reload". Here the loop comes from the process-wide policy instead: set the
  selector policy first, then drive `Server.serve()` under `asyncio.run`, which uses it.

On Linux and macOS the factory already returns a selector loop, so this launcher is equivalent
to uvicorn's CLI and is what CI would run too.

    python infrastructure/scripts/perf_api.py --port 8010
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

import uvicorn  # noqa: E402

from app.core.eventloop import use_selector_event_loop  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--log-level", default="warning",
                    help="warning keeps the p95 numbers free of logging noise; "
                         "'info' is for debugging the boot itself")
    args = ap.parse_args()

    use_selector_event_loop()
    config = uvicorn.Config("app.main:app", host=args.host, port=args.port,
                             log_level=args.log_level, loop="asyncio")
    server = uvicorn.Server(config)
    asyncio.run(server.serve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
