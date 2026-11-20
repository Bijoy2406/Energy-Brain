"""Speech-to-text for the chat microphone: Groq Whisper through the OpenAI-compatible API.

The browser's own SpeechRecognition is unavailable in some browsers (Brave blocks its speech service), so the
page records audio and ``POST /transcribe`` turns it into text. The text only fills the chat input box; it is
never sent anywhere else. Audio and transcripts are neither stored nor logged (only size, duration, language
and status).

The key is the same ``LLM_API_KEY`` the Explainer uses (``GroqClient.settings``). The model comes from
``EB_TRANSCRIBE_MODEL`` (default ``whisper-large-v3-turbo``) and is read at call time.

Whisper invents text on silence and noise ("Thank you.", repeated syllables). :func:`clean_segments` drops
segments the model itself flags as probably not speech (``no_speech_prob``) or low confidence
(``avg_logprob``), and text that is only punctuation, one repeated token or a known silence phrase such as
"Thank you." (the turbo model reports these with full confidence on digital silence).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from energy_brain.explainer.llm import GROQ_BASE_URL, GroqClient, LLMError, redact

DEFAULT_TRANSCRIBE_MODEL = "whisper-large-v3-turbo"
#: Longest transcript returned (the chat input accepts 500 characters).
MAX_TEXT_CHARS = 500
#: Segments Whisper itself marks as probably not speech, or low confidence, are dropped.
NO_SPEECH_THRESHOLD = 0.6
LOW_CONFIDENCE_LOGPROB = -1.2
UPSTREAM_TIMEOUT_S = 30.0

#: Accepted request content types (without parameters) and the file name Groq needs to detect the format.
AUDIO_TYPES = {
    "audio/webm": "audio.webm",
    "audio/ogg": "audio.ogg",
    "audio/mp4": "audio.mp4",
    "audio/mpeg": "audio.mp3",
    "audio/wav": "audio.wav",
    "audio/x-wav": "audio.wav",
}

_LANGUAGE_NAMES = {"english": "en", "en": "en"}
_PUNCTUATION_ONLY = re.compile(r"^[\W_]*$", re.UNICODE)
#: Whole-text phrases Whisper is known to invent on silence or noise. A real question about the plan is never
#: just one of these, so a transcript that is exactly one of them is treated as "no speech".
_SILENCE_PHRASES = {
    "thank you",
    "thanks",
    "thanks for watching",
    "thank you for watching",
    "you",
    "bye",
    "bye bye",
    "okay",
    "so",
}


class TranscribeError(Exception):
    """A transcription failure the router turns into an API error. ``kind`` is one of ``unavailable``,
    ``rate_limit``, ``failed``."""

    def __init__(self, kind: str, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retry_after = retry_after


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str
    duration_s: float
    model: str
    no_speech: bool


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def clean_segments(segments: list[Any]) -> str:
    """The speech Whisper is confident about, joined and trimmed; ``""`` when nothing survives."""
    kept: list[str] = []
    for seg in segments:
        text = str(_get(seg, "text", "") or "").strip()
        if not text:
            continue
        no_speech = _get(seg, "no_speech_prob")
        logprob = _get(seg, "avg_logprob")
        if isinstance(no_speech, int | float) and no_speech > NO_SPEECH_THRESHOLD:
            continue
        if isinstance(logprob, int | float) and logprob < LOW_CONFIDENCE_LOGPROB:
            continue
        kept.append(text)
    return _drop_noise(" ".join(kept).strip())


def _drop_noise(text: str) -> str:
    """Empty for punctuation-only text and for one token repeated three or more times."""
    if _PUNCTUATION_ONLY.match(text):
        return ""
    tokens = [t for t in re.split(r"\s+", re.sub(r"[^\w\s]", " ", text.lower())) if t]
    if len(tokens) >= 3 and len(set(tokens)) == 1:
        return ""
    if " ".join(tokens) in _SILENCE_PHRASES:
        return ""
    return text


def normalise_language(requested: str | None, detected: Any) -> str:
    if requested == "en":
        return requested
    name = str(detected or "").strip().lower()
    return _LANGUAGE_NAMES.get(name, name or "en")


class GroqTranscriber:
    """Groq ``audio.transcriptions``. Nothing is read until :meth:`transcribe` or :meth:`available`."""

    def __init__(self, *, base_url: str = GROQ_BASE_URL, timeout: float = UPSTREAM_TIMEOUT_S) -> None:
        self.base_url = base_url
        self.timeout = timeout

    def _key(self) -> str | None:
        try:
            return GroqClient().settings()[1]
        except LLMError:
            return None

    @property
    def model(self) -> str:
        return (os.environ.get("EB_TRANSCRIBE_MODEL") or "").strip() or DEFAULT_TRANSCRIBE_MODEL

    def available(self) -> bool:
        return bool(self._key())

    def transcribe(self, audio: bytes, content_type: str, language: str | None) -> Transcript:
        key = self._key()
        if not key:
            raise TranscribeError(
                "unavailable", "Voice input needs LLM_API_KEY on the server (a Groq key); it is not set."
            )
        import openai

        model = self.model
        kwargs: dict[str, Any] = {
            "model": model,
            "file": (AUDIO_TYPES.get(content_type, "audio.webm"), audio, content_type),
            "response_format": "verbose_json",
            "temperature": 0,
        }
        if language == "en":
            kwargs["language"] = language
        try:
            client = openai.OpenAI(api_key=key, base_url=self.base_url, max_retries=0, timeout=self.timeout)
            resp = client.audio.transcriptions.create(**kwargs)
        except openai.APITimeoutError as exc:
            raise TranscribeError("failed", "The speech service did not answer in time.") from exc
        except openai.APIConnectionError as exc:
            raise TranscribeError("failed", "Could not reach the speech service.") from exc
        except openai.APIStatusError as exc:
            status = getattr(exc, "status_code", None)
            if status == 429:
                retry = None
                headers = getattr(getattr(exc, "response", None), "headers", None) or {}
                try:
                    retry = float(headers.get("retry-after"))
                except (TypeError, ValueError):
                    pass
                raise TranscribeError("rate_limit", "The speech service is busy.", retry) from exc
            if status in (401, 403):
                raise TranscribeError(
                    "unavailable", "The speech service rejected the server's API key."
                ) from exc
            raise TranscribeError("failed", f"The speech service returned HTTP {status}.") from exc
        except Exception as exc:  # anything else from the SDK, never with the key in it
            raise TranscribeError("failed", redact(f"{type(exc).__name__}", key)) from exc

        segments = _get(resp, "segments") or []
        if segments:
            text = clean_segments(list(segments))
        else:
            text = _drop_noise(str(_get(resp, "text", "") or "").strip())
        text = text[:MAX_TEXT_CHARS]
        duration = _get(resp, "duration")
        return Transcript(
            text=text,
            language=normalise_language(language, _get(resp, "language")),
            duration_s=round(float(duration), 2) if isinstance(duration, int | float) else 0.0,
            model=model,
            no_speech=not text,
        )


__all__ = [
    "AUDIO_TYPES",
    "GroqTranscriber",
    "TranscribeError",
    "Transcript",
    "clean_segments",
    "normalise_language",
]
