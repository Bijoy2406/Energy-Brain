"""API settings (#21), read from the environment when the app is created (never at import).

- ``EB_CORS_ORIGINS`` (default none): extra allowed origins, comma-separated, added to the React dev server's
  ``http://localhost:5173`` and ``http://127.0.0.1:5173``.
- ``EB_EXPLAIN_CACHE`` (default ``fallback``): stored verified demo answers are served ``fallback`` = only
  when the live LLM call fails, ``prefer`` = first, for an exact match (demo day), or ``off``.
- ``EB_TRANSCRIBE_MODEL`` (default ``whisper-large-v3-turbo``): the Groq Whisper model behind
  ``POST /transcribe``, read at call time (see ``api/transcribe.py``).
- ``EB_WARMUP`` (default ``1``): ``0`` disables planning the demo days in a background thread at startup.

The LLM settings (``LLM_PROVIDER``, ``LLM_MODEL``, ``LLM_API_KEY``) are read by the Explainer's ``GroqClient``
at call time, as before; the API never logs or returns the key.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]

#: The React dev server (Vite default port), issue #27.
DEFAULT_CORS_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
EXPLAIN_CACHE_MODES = ("fallback", "prefer", "off")


@dataclass(frozen=True)
class ApiSettings:
    """Everything the API service needs; tests pass their own paths and limits."""

    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    explain_cache: str = "fallback"
    warmup: bool = True
    #: Weights planned for every demo day by the warm-up (1 = cheapest, 0 = cleanest).
    warmup_weights: tuple[float, ...] = (0.5, 0.0, 1.0)
    #: Maximum number of plans kept in the LRU cache (key: date, w rounded to 0.01).
    cache_size: int = 256
    #: /explain limits: requests per minute per client IP and in total, concurrent LLM calls.
    explain_per_ip_per_min: int = 5
    explain_total_per_min: int = 30
    explain_concurrency: int = 2
    #: /transcribe limits: requests per minute per client IP and in total, concurrent uploads, body size.
    transcribe_per_ip_per_min: int = 12
    transcribe_total_per_min: int = 60
    transcribe_concurrency: int = 2
    transcribe_max_bytes: int = 2 * 1024 * 1024
    #: Seconds an /explain request waits for a free LLM slot before 503.
    explain_wait_s: float = 30.0
    demo_days_path: Path = ROOT_DIR / "config" / "demo_days.yaml"
    backtest_path: Path = ROOT_DIR / "reports" / "backtest.json"
    demo_answers_path: Path = ROOT_DIR / "reports" / "explainer_demo_answers.json"
    data_dir: Path = ROOT_DIR / "data"
    models_dir: Path = ROOT_DIR / "models"

    @classmethod
    def from_env(cls, **overrides) -> ApiSettings:
        """Settings from ``EB_*`` environment variables; keyword arguments win."""
        extra_origins = tuple(
            o.strip().rstrip("/") for o in os.environ.get("EB_CORS_ORIGINS", "").split(",") if o.strip()
        )
        mode = (os.environ.get("EB_EXPLAIN_CACHE") or "fallback").strip().lower()
        if mode not in EXPLAIN_CACHE_MODES:
            raise ValueError(f"EB_EXPLAIN_CACHE must be one of {EXPLAIN_CACHE_MODES}, got {mode!r}")
        warmup = (os.environ.get("EB_WARMUP") or "1").strip().lower() not in ("0", "false", "no", "off")
        values = {
            "cors_origins": tuple(dict.fromkeys(DEFAULT_CORS_ORIGINS + extra_origins)),
            "explain_cache": mode,
            "warmup": warmup,
        }
        values.update(overrides)
        return cls(**values)
