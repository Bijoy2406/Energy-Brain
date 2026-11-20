"""FastAPI dependencies and error helpers shared by the routers (#21).

Tests and later issues override these with ``app.dependency_overrides`` (e.g. a ``FakeLLMClient`` explainer).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from energy_brain.api.service import PlanService
from energy_brain.api.transcribe import GroqTranscriber
from energy_brain.explainer import ExplainerAgent


def get_service(request: Request) -> PlanService:
    return request.app.state.service


def get_explainer() -> ExplainerAgent:
    """The live Explainer (Groq; the key is read by ``GroqClient`` at call time)."""
    return ExplainerAgent()


def get_transcriber() -> GroqTranscriber:
    """The live speech-to-text client (Groq Whisper; the key is read at call time)."""
    return GroqTranscriber()


ServiceDep = Annotated[PlanService, Depends(get_service)]
TranscriberDep = Annotated[GroqTranscriber, Depends(get_transcriber)]
ExplainerDep = Annotated[ExplainerAgent, Depends(get_explainer)]


def client_key(request: Request) -> str:
    """Rate-limit key: the client's IP as seen by the server (behind a proxy, configure uvicorn's
    ``--proxy-headers`` / ``--forwarded-allow-ips``)."""
    return request.client.host if request.client else "unknown"


def api_error(status: int, code: str, message: str, retry_after_s: float | None = None) -> HTTPException:
    detail = {"code": code, "message": message, "retry_after_s": retry_after_s}
    headers = {"Retry-After": str(max(1, round(retry_after_s)))} if retry_after_s is not None else None
    return HTTPException(status_code=status, detail=detail, headers=headers)
