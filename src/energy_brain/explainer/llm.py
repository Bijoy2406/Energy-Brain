"""LLM clients for the Explainer (#19): a ``LLMClient`` protocol, ``GroqClient`` and ``FakeLLMClient``.

``GroqClient`` calls Groq through its OpenAI-compatible chat-completions API (``openai`` package with
``base_url=https://api.groq.com/openai/v1``). Settings are read **at call time**, never at import or
construction: ``LLM_PROVIDER``, ``LLM_MODEL``, ``LLM_API_KEY`` from the environment, else from the git-ignored
``.env`` at the repo root. The key is never logged, returned or put in an error message
(:func:`redact`). The SDK's own retries are off (``max_retries=0``): the agent backs off once on a 429.

Every failure becomes an :class:`LLMError` with a ``kind`` the agent turns into a clear message:
``no_key``, ``bad_config``, ``network``, ``timeout``, ``rate_limit``, ``blocked_model``, ``bad_tool_call``,
``api_error``.
"""

from __future__ import annotations

import copy
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

DEFAULT_ROOT_DIR = Path(__file__).resolve().parents[3]
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "openai/gpt-oss-120b"


@dataclass(frozen=True)
class ToolCall:
    """One tool call requested by the model; ``arguments`` is the raw JSON string (validated by the agent)."""

    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class LLMResponse:
    """One assistant turn. Reasoning fields (gpt-oss) are dropped: only content and tool calls are kept."""

    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str | None = None
    usage: dict[str, int] | None = None


class LLMError(Exception):
    """A failed LLM request. ``message`` is safe to show (no key); ``retry_after`` in seconds for 429s."""

    def __init__(self, kind: str, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retry_after = retry_after


class LLMClient(Protocol):
    """Chat completion with function calling (OpenAI message and tool formats)."""

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *, timeout: float
    ) -> LLMResponse: ...


_SECRET = re.compile(r"(gsk_[A-Za-z0-9]+|sk-[A-Za-z0-9_-]{8,}|Bearer\s+\S+)")


def redact(text: str, secret: str | None = None) -> str:
    """``text`` with the key (and anything key-shaped) replaced by ``[redacted]``, cut to 300 characters."""
    out = str(text)
    if secret:
        out = out.replace(secret, "[redacted]")
    return _SECRET.sub("[redacted]", out)[:300]


def _dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    from dotenv import dotenv_values

    return {k: v for k, v in dotenv_values(path).items() if v is not None}


class GroqClient:
    """Groq chat completions (OpenAI-compatible). Nothing is read until :meth:`complete` is called."""

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        env_file: str | Path | None = None,
        base_url: str = GROQ_BASE_URL,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        reasoning_effort: str | None = "low",
    ) -> None:
        self._model = model
        self._api_key = api_key
        self.env_file = Path(env_file) if env_file is not None else DEFAULT_ROOT_DIR / ".env"
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort

    def settings(self) -> tuple[str, str | None]:
        """``(model, api_key)`` from arguments, then the environment, then ``.env`` (read now)."""
        env = _dotenv(self.env_file)

        def get(name: str) -> str | None:
            return os.environ.get(name) or env.get(name) or None

        provider = (get("LLM_PROVIDER") or "groq").lower()
        if provider != "groq":
            raise LLMError("bad_config", f"LLM_PROVIDER={provider!r} is not supported; only 'groq' is.")
        model = self._model or get("LLM_MODEL") or DEFAULT_MODEL
        return model, self._api_key or get("LLM_API_KEY")

    @property
    def model(self) -> str:
        try:
            return self.settings()[0]
        except LLMError:
            return self._model or DEFAULT_MODEL

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *, timeout: float
    ) -> LLMResponse:
        model, key = self.settings()
        if not key:
            raise LLMError("no_key", "LLM_API_KEY is not set (copy .env.example to .env and add a Groq key).")
        try:
            import openai
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise LLMError("api_error", "the 'openai' package is not installed") from exc

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.reasoning_effort and "gpt-oss" in model:
            kwargs["reasoning_effort"] = self.reasoning_effort
        try:
            client = openai.OpenAI(api_key=key, base_url=self.base_url, max_retries=0, timeout=timeout)
            resp = client.chat.completions.create(**kwargs)
        except openai.APITimeoutError as exc:
            raise LLMError("timeout", f"no answer within {timeout:g} s") from exc
        except openai.APIConnectionError as exc:
            raise LLMError("network", "could not reach the LLM service") from exc
        except openai.APIStatusError as exc:
            raise _status_error(exc, key) from exc
        except Exception as exc:  # anything else from the SDK, never with the key in it
            raise LLMError("api_error", redact(f"{type(exc).__name__}: {exc}", key)) from exc

        choice = resp.choices[0].message
        calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments or "")
            for tc in (choice.tool_calls or [])
            if getattr(tc, "function", None) is not None
        ]
        usage = None
        if getattr(resp, "usage", None) is not None:
            usage = {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
            }
        return LLMResponse(content=choice.content, tool_calls=calls, model=resp.model, usage=usage)


def _status_error(exc: Any, key: str) -> LLMError:
    """Map an HTTP error from the API to an ``LLMError`` (message redacted)."""
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    err = body.get("error", body) if isinstance(body, dict) else {}
    err = err if isinstance(err, dict) else {}
    code = str(err.get("code") or "")
    text = redact(str(err.get("message") or exc), key)
    if "blocked" in text.lower() or "blocked" in code:
        return LLMError("blocked_model", text)
    if status == 429:
        retry = None
        headers = getattr(getattr(exc, "response", None), "headers", None) or {}
        try:
            retry = float(headers.get("retry-after"))
        except (TypeError, ValueError):
            pass
        return LLMError("rate_limit", text, retry_after=retry)
    if code == "tool_use_failed":
        return LLMError("bad_tool_call", text)
    if status == 401:
        return LLMError("no_key", "the LLM service rejected the API key (HTTP 401)")
    return LLMError("api_error", f"HTTP {status}: {text}")


Step = LLMResponse | LLMError | Callable[[list[dict[str, Any]]], "LLMResponse | LLMError"]


class FakeLLMClient:
    """Scripted client for tests: returns (or raises) the next step on each call; records every request.

    A step is an ``LLMResponse``, an ``LLMError`` (raised), or a callable ``f(messages)`` returning either.
    """

    def __init__(self, script: list[Step], model: str = "fake-model") -> None:
        self.script = list(script)
        self.model = model
        self.requests: list[list[dict[str, Any]]] = []
        self.timeouts: list[float] = []

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], *, timeout: float
    ) -> LLMResponse:
        self.requests.append(copy.deepcopy(messages))
        self.timeouts.append(timeout)
        if not self.script:
            raise AssertionError("FakeLLMClient script exhausted")
        step = self.script.pop(0)
        if callable(step) and not isinstance(step, LLMResponse | LLMError):
            step = step(messages)
        if isinstance(step, LLMError):
            raise step
        return step
