from fastapi import APIRouter

from energy_brain.api.deps import ServiceDep
from energy_brain.api.models import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Service status")
def health(service: ServiceDep) -> HealthResponse:
    """Version, which data/models/reports are present, whether an LLM key is configured (true/false only),
    plan-cache statistics and the warm-up state."""
    return service.health()
