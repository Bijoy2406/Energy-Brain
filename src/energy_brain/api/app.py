"""Energy Brain dashboard API (#21).

Run (from the repo root, with the package installed)::

    uvicorn energy_brain.api.app:app --reload --port 8000

Then open http://localhost:8000/docs. ``create_app()`` builds an app (tests pass their own ``PlanService``);
the module-level ``app`` is the one uvicorn serves. Creating the app reads only ``EB_*`` environment variables
(``api.settings``); data, models and reports are loaded on first use, and the demo days are planned in a
background thread at startup unless ``EB_WARMUP=0``.

Routers live in ``api/routers/`` (one per resource) so #22, #23, #25 and #26 can add theirs.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from energy_brain import __version__
from energy_brain.api.routers import ROUTERS
from energy_brain.api.service import PlanService
from energy_brain.api.settings import ApiSettings

logger = logging.getLogger(__name__)

DESCRIPTION = """\
REST API of Energy Brain for the React dashboard (#27-#33). A deterministic optimiser makes every decision;
the LLM Explainer only explains the plan with numbers taken from tool outputs.

Response shapes are a contract: see `docs/api.md` and `docs/openapi.json` in the repository. Every plan says
whether it is safe to execute (`safe`) and whether a fallback forecast or signal was used (`banners`).
Backtest numbers come from `reports/backtest.json` and must be shown with their caveats.
"""


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": {"code": code, "message": message}})


def create_app(service: PlanService | None = None, settings: ApiSettings | None = None) -> FastAPI:
    """The FastAPI app. ``service`` defaults to a ``PlanService`` with ``ApiSettings.from_env()``."""
    if service is None:
        service = PlanService(settings or ApiSettings.from_env())
    settings = service.settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.warmup:
            service.start_warmup()
        yield
        service.shutdown()

    app = FastAPI(title="Energy Brain API", version=__version__, description=DESCRIPTION, lifespan=lifespan)
    app.state.service = service
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        allow_credentials=False,
    )

    from energy_brain.forecast import ForecastUnavailableError

    @app.exception_handler(ForecastUnavailableError)
    async def _no_forecast(request: Request, exc: ForecastUnavailableError) -> JSONResponse:
        logger.error("no forecast possible: %s", exc)
        return _error(
            503,
            "forecast_unavailable",
            "No forecast can be made on this server: neither data/processed nor data/sample is available.",
        )

    @app.exception_handler(Exception)
    async def _internal(request: Request, exc: Exception) -> JSONResponse:
        # Full details go to the server log only; the body never carries a traceback or a secret.
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return _error(500, "internal_error", "Internal server error; the details are in the server log.")

    for router in ROUTERS:
        app.include_router(router)
    return app


app = create_app()
