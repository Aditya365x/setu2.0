"""SETU API.

The ingest side is deliberately thin — validate, persist, enqueue, return 202.
All the expensive work happens in the worker, so a burst of two thousand
reports never blocks on the solver.
"""

import asyncio
import contextlib
import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import apply_schema, engine
from .routers import ingest, ivr, operations, ws


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    await apply_schema()

    # SEED_ON_START exists for hosted deployments, where there is no shell to
    # run `python -m seed.load` in before the first request arrives. Off by
    # default: locally `make seed` is explicit and a surprise 72,000-row insert
    # on every `docker compose up` would be unwelcome.
    #
    # Safe to leave on. Every seed step checks for existing rows first, so a
    # restart is a no-op rather than a duplicate.
    if os.getenv("SEED_ON_START", "").lower() in ("1", "true", "yes"):
        try:
            from seed.load import main as seed_main

            await seed_main()
        except Exception:
            # A failed seed must not stop the API booting. An empty board is
            # recoverable by hand; a container that will not start is not.
            logging.getLogger("setu").exception("seed on start failed; continuing")

    # Optionally run the optimiser here instead of as a separate process.
    # See config.run_worker_in_api — this exists for hosts with no
    # background-worker plan, and is a trade rather than a simplification.
    worker_tasks: list = []
    if settings.run_worker_in_api:
        try:
            from .workers.run import start_loops

            worker_tasks = await start_loops()
        except Exception:
            logging.getLogger("setu").exception(
                "could not start embedded optimiser; API will serve reads only"
            )

    yield

    for task in worker_tasks:
        task.cancel()
    for task in worker_tasks:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    await engine.dispose()


app = FastAPI(
    title="SETU",
    description=(
        "Real-time disaster early-warning and resource coordination. "
        "The allocation layer between the alert and the boots on the ground."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# The dashboard and PWA are served from separate origins in dev and from nginx
# in the compose stack; both need to reach this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest.router)
app.include_router(ivr.router)
app.include_router(operations.router)
app.include_router(ws.router)

media_root = Path(settings.media_root)
media_root.mkdir(parents=True, exist_ok=True)
app.mount(settings.media_base_url, StaticFiles(directory=str(media_root)), name="media")


PORTAL = Path(__file__).parent / "portal.html"


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def portal() -> str:
    """One page that links every surface and shows the live inbound stream.

    Useful during a demo — and during a rehearsal, where the question is
    usually "did that report actually land?" rather than anything the map can
    answer.
    """
    return PORTAL.read_text(encoding="utf-8")


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "district_id": settings.district_id,
        "routing": settings.routing_provider,
        "sms": settings.sms_provider,
        "offline_mode": settings.offline_mode,
    }
