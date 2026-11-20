"""Downloadable monthly or full-period historical replay report (FR-13, #26)."""

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from energy_brain.api.backtest_view import WeightNotInBacktest
from energy_brain.api.deps import ServiceDep, api_error, client_key
from energy_brain.api.models import ErrorBody
from energy_brain.api.report import MonthNotInBacktest
from energy_brain.api.routers.backtest import _report
from energy_brain.api.service import RateLimited

router = APIRouter(tags=["report"])


@router.get(
    "/report",
    response_class=Response,
    summary="Download savings and CO2 report",
    responses={
        200: {"content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}},
        404: {"model": ErrorBody, "description": "reports/backtest.json does not exist"},
        422: {
            "description": "Malformed query, month_not_in_backtest or weight_not_in_backtest",
            "content": {
                "application/json": {
                    "schema": {
                        "anyOf": [
                            {"$ref": "#/components/schemas/ErrorBody"},
                            {"$ref": "#/components/schemas/HTTPValidationError"},
                        ]
                    }
                }
            },
        },
        429: {"model": ErrorBody, "description": "12 downloads per client/minute; 60 overall"},
    },
)
def report(
    request: Request,
    service: ServiceDep,
    month: Annotated[
        str | None,
        Query(
            pattern=r"^[0-9]{4}-[0-9]{2}$", description="YYYY-MM inside the backtest; omit for full period"
        ),
    ] = None,
    weight: Annotated[
        float | None, Query(allow_inf_nan=False, description="A backtested weight; default: headline weight")
    ] = None,
) -> Response:
    """Three A4 pages, adjusted totals, both baselines, all caveats and provenance.

    Historical synthetic replay, not a live deployment or a guarantee. Deterministic bytes
    cached in a 32-entry LRU; every download counts toward the sliding one-minute limit.
    """
    raw = _report(service)
    try:
        content = service.report_pdf(raw, month, weight, client_key(request))
    except MonthNotInBacktest as exc:
        raise api_error(422, "month_not_in_backtest", str(exc)) from None
    except WeightNotInBacktest as exc:
        raise api_error(422, "weight_not_in_backtest", str(exc)) from None
    except RateLimited as exc:
        raise api_error(
            429,
            "rate_limited",
            "Too many report downloads; please wait and try again.",
            retry_after_s=round(exc.retry_after_s, 1),
        ) from None
    period = month or f"{raw['config']['first_day']}_to_{raw['config']['last_day']}"
    return Response(
        content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="gridpilot-report-{period}.pdf"',
        },
    )
