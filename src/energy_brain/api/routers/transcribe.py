import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from starlette.concurrency import run_in_threadpool

from energy_brain.api.deps import ServiceDep, TranscriberDep, api_error, client_key
from energy_brain.api.models import ErrorBody, TranscribeResponse
from energy_brain.api.service import RateLimited
from energy_brain.api.transcribe import AUDIO_TYPES, TranscribeError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["transcribe"])

_BINARY_BODY = {
    "requestBody": {
        "required": True,
        "description": (
            "The raw audio bytes (no multipart): up to 2 MB, content type audio/webm, audio/ogg, audio/mp4, "
            "audio/mpeg or audio/wav (codec parameters such as `;codecs=opus` are accepted)."
        ),
        "content": {t: {"schema": {"type": "string", "format": "binary"}} for t in AUDIO_TYPES},
    }
}


@router.post(
    "/transcribe",
    response_model=TranscribeResponse,
    summary="Turn a short voice recording into text for the chat box",
    openapi_extra=_BINARY_BODY,
    responses={
        413: {"model": ErrorBody, "description": "audio larger than the limit (audio_too_large)"},
        415: {"model": ErrorBody, "description": "not a supported audio type (unsupported_audio)"},
        422: {"model": ErrorBody, "description": "empty body (empty_audio)"},
        429: {"model": ErrorBody, "description": "too many requests (rate_limited)"},
        502: {"model": ErrorBody, "description": "the speech service failed (transcription_failed)"},
        503: {"model": ErrorBody, "description": "no key on the server, or all slots busy"},
    },
)
async def transcribe(
    request: Request,
    service: ServiceDep,
    transcriber: TranscriberDep,
    language: Annotated[
        Literal["auto", "en"],
        Query(description="spoken language; auto lets the speech model detect it"),
    ] = "auto",
) -> TranscribeResponse:
    """The page records a few seconds of audio and posts the raw bytes. The text is only put into the chat
    input box by the page; it is never sent to the planner or stored. Silence and noise give `no_speech: true`
    and an empty `text`, not an error. Audio is sent to Groq for transcription and is not stored or logged."""
    settings = service.settings
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type not in AUDIO_TYPES:
        raise api_error(415, "unsupported_audio", f"Send audio as one of: {', '.join(sorted(AUDIO_TYPES))}.")
    limit = settings.transcribe_max_bytes
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise api_error(413, "audio_too_large", f"The recording is larger than {limit // (1024 * 1024)} MB.")
    try:
        service.transcribe_limiter.hit(client_key(request))
    except RateLimited as exc:
        who = "from this client" if exc.scope == "client" else "in total"
        raise api_error(
            429,
            "rate_limited",
            f"Too many voice requests {who}; please wait {exc.retry_after_s:.0f} s and try again.",
            retry_after_s=round(exc.retry_after_s, 1),
        ) from None

    audio = bytearray()
    async for chunk in request.stream():
        audio.extend(chunk)
        if len(audio) > limit:
            raise api_error(
                413, "audio_too_large", f"The recording is larger than {limit // (1024 * 1024)} MB."
            )
    if not audio:
        raise api_error(422, "empty_audio", "The recording is empty.")

    if not service.transcribe_slots.acquire(blocking=False):
        raise api_error(503, "busy", "Voice input is busy; please try again in a moment.", 3.0)
    try:
        result = await run_in_threadpool(
            transcriber.transcribe, bytes(audio), media_type, None if language == "auto" else language
        )
    except TranscribeError as exc:
        logger.warning("transcribe failed: kind=%s bytes=%d", exc.kind, len(audio))
        if exc.kind == "rate_limit":
            raise api_error(
                429, "rate_limited", "The speech service is busy; try again shortly.", exc.retry_after
            ) from None
        if exc.kind == "unavailable":
            raise api_error(503, "transcription_unavailable", exc.message) from None
        raise api_error(502, "transcription_failed", exc.message) from None
    finally:
        service.transcribe_slots.release()
    logger.info(
        "transcribe ok: bytes=%d duration_s=%.1f language=%s no_speech=%s",
        len(audio),
        result.duration_s,
        result.language,
        result.no_speech,
    )
    return TranscribeResponse(
        text=result.text,
        language=result.language,
        duration_s=result.duration_s,
        model=result.model,
        no_speech=result.no_speech,
    )
