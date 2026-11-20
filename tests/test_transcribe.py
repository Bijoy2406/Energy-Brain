"""POST /transcribe: raw audio in, text out (Groq Whisper behind it; no network in these tests)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import openai
import pytest
from fastapi.testclient import TestClient

from energy_brain.api import transcribe as tr
from energy_brain.api.app import create_app
from energy_brain.api.deps import get_transcriber
from energy_brain.api.service import PlanService
from energy_brain.api.settings import ApiSettings
from energy_brain.api.transcribe import GroqTranscriber, TranscribeError, Transcript, clean_segments

SECRET = "gsk_test_secret_value_123456"
AUDIO = b"\x1aE\xdf\xa3fake-webm-bytes"


class FakeTranscriber:
    def __init__(self, result: Transcript | TranscribeError | None = None) -> None:
        self.result = result or Transcript("Why the generator?", "en", 2.5, "whisper-test", False)
        self.calls: list[tuple[int, str, str | None]] = []

    def transcribe(self, audio: bytes, content_type: str, language: str | None) -> Transcript:
        self.calls.append((len(audio), content_type, language))
        if isinstance(self.result, TranscribeError):
            raise self.result
        return self.result


def make_client(
    tmp_path, fake: FakeTranscriber | None = None, **settings
) -> tuple[TestClient, FakeTranscriber, PlanService]:
    fake = fake or FakeTranscriber()
    values = {
        "warmup": False,
        "data_dir": tmp_path / "data",
        "models_dir": tmp_path / "models",
        "backtest_path": tmp_path / "backtest.json",
        "demo_days_path": tmp_path / "demo_days.yaml",
        "demo_answers_path": tmp_path / "answers.json",
    }
    values.update(settings)
    service = PlanService(ApiSettings(**values))
    app = create_app(service)
    app.dependency_overrides[get_transcriber] = lambda: fake
    return TestClient(app), fake, service


def post(client, body=AUDIO, content_type="audio/webm;codecs=opus", language=None):
    url = "/transcribe" + (f"?language={language}" if language else "")
    return client.post(url, content=body, headers={"Content-Type": content_type})


# ------------------------------------------------------------------------------------------- the endpoint


def test_transcribes_raw_audio_and_passes_the_language(tmp_path):
    client, fake, _ = make_client(tmp_path)
    r = post(client, language="en")
    assert r.status_code == 200
    assert r.json() == {
        "text": "Why the generator?",
        "language": "en",
        "duration_s": 2.5,
        "model": "whisper-test",
        "no_speech": False,
    }
    assert fake.calls == [(len(AUDIO), "audio/webm", "en")]  # codec parameter stripped, language forwarded
    post(client)  # auto: no language is passed to the speech model
    assert fake.calls[-1][2] is None


@pytest.mark.parametrize("content_type", ["text/plain", "application/json", "video/webm", ""])
def test_rejects_unsupported_content_types(tmp_path, content_type):
    client, fake, _ = make_client(tmp_path)
    r = post(client, content_type=content_type)
    assert r.status_code == 415 and r.json()["detail"]["code"] == "unsupported_audio"
    assert fake.calls == []


def test_empty_body_is_422(tmp_path):
    client, fake, _ = make_client(tmp_path)
    r = post(client, body=b"")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "empty_audio"
    assert fake.calls == []


def test_oversized_audio_is_413_by_header_and_while_streaming(tmp_path):
    client, fake, _ = make_client(tmp_path, transcribe_max_bytes=100)
    r = post(client, body=b"x" * 101)
    assert r.status_code == 413 and r.json()["detail"]["code"] == "audio_too_large"

    def chunks():  # no Content-Length: the cap must still hold while reading
        for _ in range(5):
            yield b"x" * 30

    r = client.post("/transcribe", content=chunks(), headers={"Content-Type": "audio/webm"})
    assert r.status_code == 413 and r.json()["detail"]["code"] == "audio_too_large"
    assert fake.calls == []
    assert post(client, body=b"x" * 100).status_code == 200


def test_silence_returns_no_speech_not_an_error(tmp_path):
    client, _, _ = make_client(tmp_path, FakeTranscriber(Transcript("", "en", 2.0, "whisper-test", True)))
    r = post(client)
    assert r.status_code == 200 and r.json()["text"] == "" and r.json()["no_speech"] is True


def test_missing_key_is_503_with_a_clear_message(tmp_path):
    client, _, _ = make_client(tmp_path, FakeTranscriber(TranscribeError("unavailable", "needs LLM_API_KEY")))
    r = post(client)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "transcription_unavailable"


def test_upstream_rate_limit_and_failure_are_mapped(tmp_path):
    client, fake, _ = make_client(tmp_path, FakeTranscriber(TranscribeError("rate_limit", "busy", 7.0)))
    r = post(client)
    assert r.status_code == 429 and r.json()["detail"]["code"] == "rate_limited"
    assert r.json()["detail"]["retry_after_s"] == 7.0 and r.headers["retry-after"] == "7"
    fake.result = TranscribeError("failed", "The speech service returned HTTP 500.")
    r = post(client)
    assert r.status_code == 502 and r.json()["detail"]["code"] == "transcription_failed"


def test_per_client_rate_limit(tmp_path):
    client, fake, _ = make_client(tmp_path, transcribe_per_ip_per_min=2)
    assert post(client).status_code == 200 and post(client).status_code == 200
    r = post(client)
    assert r.status_code == 429 and r.json()["detail"]["code"] == "rate_limited"
    assert r.json()["detail"]["retry_after_s"] > 0
    assert len(fake.calls) == 2


def test_busy_when_all_upload_slots_are_taken(tmp_path):
    client, fake, service = make_client(tmp_path, transcribe_concurrency=1)
    assert service.transcribe_slots.acquire(blocking=False)
    r = post(client)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "busy"
    service.transcribe_slots.release()
    assert post(client).status_code == 200 and len(fake.calls) == 1


def test_slot_is_released_after_an_upstream_error(tmp_path):
    client, fake, _ = make_client(
        tmp_path, FakeTranscriber(TranscribeError("failed", "x")), transcribe_concurrency=1
    )
    assert post(client).status_code == 502
    fake.result = Transcript("ok", "en", 1.0, "m", False)
    assert post(client).status_code == 200


def test_health_reports_whether_voice_input_can_work(tmp_path, monkeypatch):
    client, _, _ = make_client(tmp_path)
    monkeypatch.setenv("LLM_API_KEY", SECRET)
    body = client.get("/health").json()
    assert body["transcription_available"] is True and SECRET not in str(body)
    monkeypatch.delenv("LLM_API_KEY")
    monkeypatch.setattr("energy_brain.explainer.llm._dotenv", lambda path: {})
    assert client.get("/health").json()["transcription_available"] is False


def test_transcripts_and_audio_are_never_logged(tmp_path, caplog):
    client, _, _ = make_client(
        tmp_path, FakeTranscriber(Transcript("secret factory question", "en", 2.0, "m", False))
    )
    with caplog.at_level(logging.DEBUG):
        assert post(client).status_code == 200
    assert "secret factory question" not in caplog.text
    assert "fake-webm-bytes" not in caplog.text


# ------------------------------------------------------------------------------- silence and noise filtering


def seg(text, no_speech=0.0, logprob=-0.3):
    return {"text": text, "no_speech_prob": no_speech, "avg_logprob": logprob}


def test_clean_segments_keeps_confident_speech_and_joins_it():
    assert (
        clean_segments([seg(" Why did you run"), seg(" the generator at 7pm?")])
        == "Why did you run the generator at 7pm?"
    )


def test_clean_segments_drops_what_the_model_flags_as_not_speech_or_unsure():
    assert clean_segments([seg("Thanks for listening", no_speech=0.9)]) == ""
    assert clean_segments([seg("garbled", logprob=-2.0)]) == ""
    assert (
        clean_segments([seg("real question here", no_speech=0.9), seg("kept words", no_speech=0.1)])
        == "kept words"
    )


@pytest.mark.parametrize(
    "text", ["...", "  ", "---", "la la la la", "Thank you.", "you", "Thanks for watching!", "Bye."]
)
def test_clean_segments_drops_known_silence_hallucinations(text):
    assert clean_segments([seg(text)]) == ""


def test_clean_segments_keeps_a_real_question_that_contains_those_words():
    assert clean_segments([seg("Thank you, why did the battery charge at 4:30?")]).startswith(
        "Thank you, why"
    )


def test_normalise_language_prefers_the_request_then_the_detected_name():
    assert tr.normalise_language("en", "french") == "en"
    assert tr.normalise_language(None, "english") == "en"
    assert tr.normalise_language(None, None) == "en"


# --------------------------------------------------------------------------- the real client (mocked SDK)


class FakeSDK:
    """Stands in for ``openai.OpenAI``; records the request and returns or raises what the test sets."""

    outcome: object = None
    seen: dict = {}

    def __init__(self, **kwargs):
        FakeSDK.seen = {"client": kwargs}
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        FakeSDK.seen["request"] = kwargs
        if isinstance(FakeSDK.outcome, Exception):
            raise FakeSDK.outcome
        return FakeSDK.outcome


@pytest.fixture
def groq(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", SECRET)
    monkeypatch.delenv("EB_TRANSCRIBE_MODEL", raising=False)
    monkeypatch.setattr(openai, "OpenAI", FakeSDK)
    FakeSDK.seen = {}
    return GroqTranscriber()


def status_error(status, headers=None):
    request = SimpleNamespace(method="POST", url="https://example.invalid")
    response = SimpleNamespace(status_code=status, headers=headers or {}, request=request)
    return openai.APIStatusError("upstream said no", response=response, body=None)


def test_groq_request_shape_and_filtered_result(groq):
    FakeSDK.outcome = SimpleNamespace(
        text=" Thank you.", language="english", duration=3.4, segments=[seg("Why is the generator running?")]
    )
    result = groq.transcribe(AUDIO, "audio/webm", "en")
    request = FakeSDK.seen["request"]
    assert request["model"] == "whisper-large-v3-turbo" and request["language"] == "en"
    assert request["response_format"] == "verbose_json" and request["temperature"] == 0
    assert request["file"] == ("audio.webm", AUDIO, "audio/webm")
    assert FakeSDK.seen["client"]["max_retries"] == 0 and "groq.com" in FakeSDK.seen["client"]["base_url"]
    assert (result.text, result.language, result.duration_s, result.no_speech) == (
        "Why is the generator running?",
        "en",
        3.4,
        False,
    )


def test_groq_model_can_be_overridden_and_language_is_optional(groq, monkeypatch):
    monkeypatch.setenv("EB_TRANSCRIBE_MODEL", "whisper-large-v3")
    FakeSDK.outcome = SimpleNamespace(text="hello there", language="english", duration=1.0, segments=[])
    result = groq.transcribe(AUDIO, "audio/ogg", None)
    assert (
        FakeSDK.seen["request"]["model"] == "whisper-large-v3" and "language" not in FakeSDK.seen["request"]
    )
    assert FakeSDK.seen["request"]["file"][0] == "audio.ogg"
    assert result.text == "hello there" and result.language == "en" and result.model == "whisper-large-v3"


def test_groq_silence_becomes_no_speech(groq):
    FakeSDK.outcome = SimpleNamespace(
        text=" Thank you.", language="english", duration=2.0, segments=[seg(" Thank you.")]
    )
    result = groq.transcribe(AUDIO, "audio/webm", None)
    assert result.text == "" and result.no_speech is True


def test_groq_without_a_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setattr("energy_brain.explainer.llm._dotenv", lambda path: {})
    t = GroqTranscriber()
    assert t.available() is False
    with pytest.raises(TranscribeError) as err:
        t.transcribe(AUDIO, "audio/webm", None)
    assert err.value.kind == "unavailable"


@pytest.mark.parametrize(
    ("outcome", "kind"),
    [
        (status_error(429, {"retry-after": "9"}), "rate_limit"),
        (status_error(401), "unavailable"),
        (status_error(500), "failed"),
        (openai.APITimeoutError(request=SimpleNamespace(method="POST", url="x")), "failed"),
        (openai.APIConnectionError(request=SimpleNamespace(method="POST", url="x")), "failed"),
        (RuntimeError(f"boom with {SECRET} inside"), "failed"),
    ],
)
def test_groq_errors_are_mapped_and_never_leak_the_key(groq, outcome, kind):
    FakeSDK.outcome = outcome
    with pytest.raises(TranscribeError) as err:
        groq.transcribe(AUDIO, "audio/webm", None)
    assert err.value.kind == kind
    assert SECRET not in err.value.message
    if kind == "rate_limit":
        assert err.value.retry_after == 9.0
