from fastapi import APIRouter, Request

from energy_brain.api.deps import ServiceDep, api_error, client_key
from energy_brain.api.models import CloudRequest, CloudResponse, ErrorBody, PlanRequest, PlanResponse
from energy_brain.api.service import InvalidOverrides, RateLimited

router = APIRouter(tags=["plan"])


@router.post(
    "/plan",
    response_model=PlanResponse,
    summary="Plan a day at a cost/carbon weight",
    responses={
        422: {
            "model": ErrorBody,
            "description": "invalid operator override (normal validation uses FastAPI)",
        },
        429: {"model": ErrorBody, "description": "too many uncached override computations"},
        503: {"model": ErrorBody, "description": "no forecast could be made (not even sample data)"},
    },
)
def plan(req: PlanRequest, request: Request, service: ServiceDep) -> PlanResponse:
    """Runs (or reads from the cache) the Forecast, Signal and Asset agents, the Planner and the Risk agent
    for `date` at `w` rounded to 0.01. Unsafe or fallback plans are returned with 200 and flagged: check
    `safe` and show `banners`."""
    try:
        if req.overrides:
            return service.override_response(req.date, req.w, req.overrides, client_key(request))
        return service.plan_response(req.date, req.w)
    except InvalidOverrides as exc:
        raise api_error(422, "invalid_overrides", str(exc)) from None
    except RateLimited as exc:
        raise _limited(exc) from None


def _limited(exc: RateLimited):
    return api_error(
        429,
        "rate_limited",
        "Too many what-if computations; please wait and try again.",
        retry_after_s=round(exc.retry_after_s, 1),
    )


@router.post(
    "/simulate/cloud",
    response_model=CloudResponse,
    summary="Re-plan a historical replay after a simulated cloud event",
    responses={
        422: {"model": ErrorBody, "description": "invalid simulated cloud event"},
        429: {"model": ErrorBody, "description": "too many uncached simulated cloud events"},
        503: {"model": ErrorBody, "description": "no forecast could be made"},
    },
)
def cloud(req: CloudRequest, request: Request, service: ServiceDep) -> CloudResponse:
    """A simulated cloud event reduces the historical forecast, not observed or live weather data.

    Freeze controllable decisions before the event, then run Planner and Risk. A failed re-plan is
    returned with 200 and honest diagnostic banners; always check safe and risk_ok.
    """
    try:
        return service.cloud_response(req, client_key(request))
    except RateLimited as exc:
        raise _limited(exc) from None
