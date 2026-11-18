from fastapi import APIRouter

from energy_brain.api.deps import ServiceDep
from energy_brain.api.models import ErrorBody, PlanRequest, PlanResponse

router = APIRouter(tags=["plan"])


@router.post(
    "/plan",
    response_model=PlanResponse,
    summary="Plan a day at a cost/carbon weight",
    responses={503: {"model": ErrorBody, "description": "no forecast could be made (not even sample data)"}},
)
def plan(req: PlanRequest, service: ServiceDep) -> PlanResponse:
    """Runs (or reads from the cache) the Forecast, Signal and Asset agents, the Planner and the Risk agent
    for `date` at `w` rounded to 0.01. Unsafe or fallback plans are returned with 200 and flagged: check
    `safe` and show `banners`."""
    return service.plan_response(req.date, req.w)
