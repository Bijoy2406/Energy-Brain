"""Cost versus CO2 frontier using the shared plan service."""

from datetime import date as Date
from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import AfterValidator

from energy_brain.api.deps import ServiceDep, api_error, client_key
from energy_brain.api.models import ErrorBody, ParetoResponse, PlanRequest
from energy_brain.api.service import RateLimited

router = APIRouter(tags=["pareto"])


@router.get(
    "/pareto",
    response_model=ParetoResponse,
    summary="Cost versus CO2 Pareto frontier",
    responses={
        429: {"model": ErrorBody, "description": "too many uncached computations"},
        503: {"model": ErrorBody, "description": "no forecast could be made (not even sample data)"},
    },
)
def pareto(
    date: Annotated[
        Date,
        AfterValidator(PlanRequest._in_range),
        Query(
            description="planning day, YYYY-MM-DD, from 2022-01-01 to tomorrow (site time)",
        ),
    ],
    request: Request,
    service: ServiceDep,
) -> ParetoResponse:
    """Solve the fixed weight grid sequentially. Totals are planned at P50, before settlement.
    Unsafe plans remain visible but cannot enter the frontier. Cache hits are free."""
    try:
        return service.pareto_response(date, client_key(request))
    except RateLimited as exc:
        who = "from this client" if exc.scope == "client" else "in total"
        raise api_error(
            429,
            "rate_limited",
            f"Too many Pareto computations {who}; please wait {exc.retry_after_s:.0f} s and try again.",
            retry_after_s=round(exc.retry_after_s, 1),
        ) from None
