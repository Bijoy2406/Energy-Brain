from fastapi import APIRouter

from energy_brain.api.deps import ServiceDep
from energy_brain.api.models import DaysResponse

router = APIRouter(tags=["days"])


@router.get("/days", response_model=DaysResponse, summary="Demo days")
def days(service: ServiceDep) -> DaysResponse:
    """The demo days of `config/demo_days.yaml` plus tomorrow (always a fallback forecast), with weekday,
    workday/holiday flags, outage blocks and the forecast tier (predicted until the day has been planned)."""
    return service.days()
