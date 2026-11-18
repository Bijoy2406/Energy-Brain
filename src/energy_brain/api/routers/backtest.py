from typing import Annotated, Any

from fastapi import APIRouter, Query

from energy_brain.api.backtest_view import WeightNotInBacktest, days_view, summary_view
from energy_brain.api.deps import ServiceDep, api_error
from energy_brain.api.models import BacktestDaysResponse, BacktestSummary, ErrorBody
from energy_brain.api.service import PlanService

router = APIRouter(tags=["backtest"])

_MISSING = {404: {"model": ErrorBody, "description": "reports/backtest.json does not exist"}}


def _report(service: PlanService) -> dict[str, Any]:
    report = service.backtest()
    if report is None:
        raise api_error(
            404,
            "backtest_unavailable",
            "No backtest report on this server (reports/backtest.json is missing). "
            "Run: python -m energy_brain.evaluator",
        )
    return report


@router.get("/backtest", response_model=BacktestSummary, summary="Backtest summary", responses=_MISSING)
def backtest(
    service: ServiceDep,
    include_sha: Annotated[
        bool, Query(description="include the sha256 maps of data, models and config")
    ] = False,
) -> BacktestSummary:
    """Caveats, headline lines, summary by weight, forecast metrics and provenance of the 2024 backtest."""
    return summary_view(_report(service), include_sha=include_sha)


@router.get(
    "/backtest/days",
    response_model=BacktestDaysResponse,
    summary="Backtest per-day series",
    responses=_MISSING,
)
def backtest_days(
    service: ServiceDep,
    weight: Annotated[
        float | None, Query(ge=0.0, le=1.0, description="optimiser weight; default: the headline")
    ] = None,
) -> BacktestDaysResponse:
    """Per day: adjusted and raw cost and CO2 of the optimiser (at `weight`) and of both baselines. A weight
    that was not run in the backtest gives 422 with `detail.code = weight_not_in_backtest`."""
    report = _report(service)
    try:
        return days_view(report, weight)
    except WeightNotInBacktest as exc:
        raise api_error(422, "weight_not_in_backtest", str(exc)) from None
