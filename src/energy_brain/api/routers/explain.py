from fastapi import APIRouter, Request

from energy_brain.api.deps import ExplainerDep, ServiceDep, api_error, client_key
from energy_brain.api.models import ErrorBody, ExplainRequest, ExplainResponse
from energy_brain.api.service import RateLimited, ServiceBusy

router = APIRouter(tags=["explain"])


@router.post(
    "/explain",
    response_model=ExplainResponse,
    summary="Ask the Explainer about a plan",
    responses={
        429: {"model": ErrorBody, "description": "too many questions (per client or in total)"},
        503: {"model": ErrorBody, "description": "all LLM slots busy, or no forecast could be made"},
    },
)
def explain(
    req: ExplainRequest,
    request: Request,
    service: ServiceDep,
    agent: ExplainerDep,
) -> ExplainResponse:
    """Answers with numbers traced to tool outputs only (`verified`, `provenance`), or a refusal. LLM problems
    (no key, rate limit, network...) come back as 200 with `error` set and a clear `answer`. A stored verified
    demo answer may be served instead (`cached: true`, see docs/api.md)."""
    try:
        service.limiter.hit(client_key(request))
    except RateLimited as exc:
        who = "from this client" if exc.scope == "client" else "in total"
        raise api_error(
            429,
            "rate_limited",
            f"Too many questions {who}; please wait {exc.retry_after_s:.0f} s and try again.",
            retry_after_s=round(exc.retry_after_s, 1),
        ) from None
    try:
        return service.explain(req, agent)
    except ServiceBusy:
        raise api_error(503, "busy", "The Explainer is busy; please try again shortly.", 5.0) from None
