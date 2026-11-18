"""Forecast agent (issue #14): trained solar/load quantile models with a flagged persistence fallback.

``ModelForecastAgent.run(start) -> Forecast`` satisfies ``energy_brain.interfaces.ForecastAgent``.

Tiers (first one that works wins; see ``docs/interfaces.md`` section "Forecast agent rules"):

0. **Model**: ``solar.predict`` + ``load.predict``. Needs a midnight ``start``, all six model files in
   ``models_dir`` and complete weather/PV/load history for the target day and the 7 days before it (the
   lag features). ``fallback_used=False``, ``note=None``. It never lets ``predict()`` auto-train: missing
   model files are checked first and trigger the fallback.
1. **Persistence**: solar from the same slot 1 day earlier, load from the same slot 7 days earlier (same
   weekday). ``fallback_used=True``.
2. **Analog day**: when no recent day exists (start outside the data range), the same calendar day in the
   latest year with data (load shifted to the same weekday), else the latest available day.
3. Tiers 1-2 read ``data/processed`` when all three files exist, otherwise the committed ``data/sample``.
4. No data at all: ``ForecastUnavailableError``.

Every fallback ``note`` has the stable machine-readable format built by :func:`format_note` and read back by
:func:`parse_note`::

    FORECAST FALLBACK: reason=<code>[,<code>...]; method=<persistence|analog_day>; source=<...>;
    solar_from=<YYYY-MM-DD>; load_from=<YYYY-MM-DD>; data_range=<YYYY-MM-DD..YYYY-MM-DD>; detail=<text>

(all on one line). Reason codes are the ``REASON_*`` constants below.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from energy_brain.config import SiteConfig, load_site_config
from energy_brain.forecast import load as load_model
from energy_brain.forecast import solar as solar_model
from energy_brain.schemas import Forecast, QuantileSeries
from energy_brain.timeaxis import ensure_start, n_slots, site_tz, slot_minutes, slot_times

logger = logging.getLogger(__name__)

DEFAULT_ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_MODELS_DIR = DEFAULT_ROOT_DIR / "models"
DEFAULT_DATA_DIR = DEFAULT_ROOT_DIR / "data"

#: First words of every fallback note.
NOTE_PREFIX = "FORECAST FALLBACK:"
#: Keys of a fallback note, in order. ``detail`` is always last and is free text.
NOTE_KEYS = ("reason", "method", "source", "solar_from", "load_from", "data_range", "detail")

# Reason codes (stable; the Explainer quotes them).
REASON_NON_MIDNIGHT = "non_midnight_start"  # start is not 00:00 local; models only forecast whole days
REASON_AXIS = "axis_mismatch"  # config time axis is not the 96 x 15 min the models were trained on
REASON_OUT_OF_RANGE = "out_of_range"  # target day outside the first..last fully covered day
REASON_HISTORY = "history_incomplete"  # target day or one of the 7 lag days has gaps
REASON_MODELS = "models_missing"  # one or more models/{solar,load}_p{10,50,90}.txt missing
REASON_PREDICT = "prediction_error"  # predict() raised
REASON_OUTPUT = "invalid_output"  # predict() returned NaN/inf, wrong length or badly unordered quantiles
REASON_NO_PROCESSED = "processed_data_missing"  # data/processed incomplete, data/sample used instead

#: Quantile violations up to this many kW are repaired silently; larger ones are an error.
QUANTILE_TOL_KW = 1e-6
#: Days of history the model's lag features need before the target day (load lag 168 h).
LAG_DAYS = 7
SOLAR_RECENT_DELTAS = tuple(range(1, 8))  # persistence: yesterday, then up to a week back
LOAD_RECENT_DELTAS = (7, 14, 21, 28)  # persistence: same weekday, up to four weeks back
SOLAR_BAND_DAYS = 7  # P10/P90 from this many consecutive days ending at the source day
LOAD_BAND_WEEKS = 4  # P10/P90 from this many same-weekday days ending at the source day

MODEL_FILES = tuple(f"{kind}_p{q}.txt" for kind in ("solar", "load") for q in (10, 50, 90))
DATA_FILES = {"weather": "weather.parquet", "pv": "pv.parquet", "load": "load.parquet"}


class ForecastUnavailableError(RuntimeError):
    """Not even the committed ``data/sample`` history is available, so no forecast can be made."""


class _InvalidOutput(ValueError):
    """Forecast series are not usable (NaN/inf, negative or out-of-order beyond tolerance)."""


def format_note(fields: dict[str, str]) -> str:
    """Build a fallback note from ``fields`` (keys from ``NOTE_KEYS``) in the stable order."""
    parts = []
    for key in NOTE_KEYS:
        value = str(fields.get(key, "")).replace(";", ",").replace("\n", " ").strip()
        parts.append(f"{key}={value}")
    return f"{NOTE_PREFIX} " + "; ".join(parts)


def parse_note(note: str | None) -> dict[str, str] | None:
    """Inverse of :func:`format_note`: dict of note fields, or None if ``note`` is not a fallback note."""
    if not note or not note.startswith(NOTE_PREFIX):
        return None
    out: dict[str, str] = {}
    body = note[len(NOTE_PREFIX) :].strip()
    head, sep, detail = body.partition("; detail=")
    for part in head.split("; "):
        key, _, value = part.partition("=")
        out[key.strip()] = value.strip()
    if sep:
        out["detail"] = detail.strip()
    return out


def repair_quantiles(
    p10: Iterable[float], p50: Iterable[float], p90: Iterable[float], tol: float = QUANTILE_TOL_KW
) -> tuple[list[float], list[float], list[float]]:
    """Return ``(p10, p50, p90)`` clipped to >= 0 and ordered, repairing violations up to ``tol`` kW.

    Raises ``_InvalidOutput`` for NaN/inf, values below ``-tol`` or out-of-order violations above ``tol``.
    """
    a, b, c = (np.asarray(list(x), dtype=float) for x in (p10, p50, p90))
    if not (len(a) == len(b) == len(c)):
        raise _InvalidOutput(f"quantile lengths differ: {len(a)}, {len(b)}, {len(c)}")
    stacked = np.stack([a, b, c])
    if not np.isfinite(stacked).all():
        raise _InvalidOutput("forecast contains NaN or inf")
    if (stacked < -tol).any():
        raise _InvalidOutput(f"forecast has negative values down to {stacked.min():.6g} kW")
    worst = max(float(np.max(a - b, initial=0.0)), float(np.max(b - c, initial=0.0)))
    if worst > tol:
        raise _InvalidOutput(f"quantiles out of order by up to {worst:.6g} kW")
    a = np.clip(a, 0.0, None)
    b = np.maximum(np.clip(b, 0.0, None), a)
    c = np.maximum(np.clip(c, 0.0, None), b)
    return a.tolist(), b.tolist(), c.tolist()


def _indexed(df: pd.DataFrame, tz) -> pd.DataFrame:
    """Copy of ``df`` indexed by a tz-aware (site tz) timestamp, sorted, without duplicate timestamps."""
    out = df.set_index("timestamp") if "timestamp" in df.columns else df.copy()
    idx = pd.DatetimeIndex(out.index)
    idx = idx.tz_localize(tz) if idx.tz is None else idx.tz_convert(tz)
    out.index = idx.rename("timestamp")
    out = out[~out.index.duplicated(keep="first")].sort_index()
    return out


def _full_days(index: pd.DatetimeIndex, per_day: int) -> set[date]:
    """Calendar days with at least ``per_day`` distinct periods present (slots or hours)."""
    if len(index) == 0:
        return set()
    counts = pd.Series(1, index=index).groupby(index.date).size()
    return {d for d, n in counts.items() if n >= per_day}


@dataclass
class _History:
    weather: pd.DataFrame  # indexed by timestamp, hourly (or finer) weather columns
    pv: pd.Series  # pv_kw, 15 min, NaNs dropped
    load: pd.Series  # base_kw, 15 min, NaNs dropped
    source: str  # "processed", "sample", "injected" or a "+"-joined mix
    covered: set[date] = field(default_factory=set)  # days complete in all three

    @property
    def data_range(self) -> str:
        if not self.covered:
            return "none"
        return f"{min(self.covered).isoformat()}..{max(self.covered).isoformat()}"


class ModelForecastAgent:
    """Forecast agent (#14): P10/P50/P90 solar (kW AC) and inflexible base load (kW, excl. HVAC and EV).

    ``weather_df`` / ``pv_df`` / ``load_df`` replace the files in ``data_dir`` (tests, notebooks);
    ``models_dir`` defaults to ``models/``. Data is read lazily on the first ``run`` and cached, so
    construction never touches the git-ignored ``data/processed`` or ``models/``.
    """

    def __init__(
        self,
        *,
        weather_df: pd.DataFrame | None = None,
        pv_df: pd.DataFrame | None = None,
        load_df: pd.DataFrame | None = None,
        models_dir: Path | None = None,
        data_dir: Path | None = None,
        config: SiteConfig | None = None,
    ) -> None:
        self._injected = {"weather": weather_df, "pv": pv_df, "load": load_df}
        self.models_dir = Path(models_dir) if models_dir is not None else DEFAULT_MODELS_DIR
        self.data_dir = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
        self.config = config if config is not None else load_site_config()
        self._history: _History | None = None
        self._processed_missing = False

    # ------------------------------------------------------------------ public

    def run(self, start: datetime) -> Forecast:
        """Forecast for the ``n_slots()`` slots from ``start``; never raises for missing models or data.

        Raises ``ValueError`` for a naive or off-slot ``start`` (programmer error) and
        ``ForecastUnavailableError`` only if not even ``data/sample`` can be read.
        """
        local = ensure_start(start, self.config)
        hist = self._load_history()
        reasons, details = self._model_blockers(local, hist)
        if not reasons:
            try:
                return self._model_forecast(local, hist)
            except _InvalidOutput as exc:
                reasons, details = [REASON_OUTPUT], [str(exc)]
            except Exception as exc:  # any model/library failure must degrade, not crash planning
                logger.exception("forecast model failed for %s", local.isoformat())
                reasons, details = [REASON_PREDICT], [f"{type(exc).__name__}: {exc}"]
        if self._processed_missing:
            reasons.append(REASON_NO_PROCESSED)
            details.append(f"{self.data_dir / 'processed'} incomplete, using data/sample")
        return self._persistence_forecast(local, hist, reasons, details)

    # ------------------------------------------------------------------ data

    def _read_dir(self, directory: Path, names: dict[str, str]) -> dict[str, pd.DataFrame] | None:
        """All needed frames from ``directory`` or None if any file is missing / unreadable."""
        frames = {}
        for key, filename in names.items():
            path = directory / filename
            if not path.exists():
                return None
            try:
                frames[key] = pd.read_parquet(path)
            except Exception as exc:
                logger.warning("cannot read %s: %s", path, exc)
                return None
        return frames

    def _load_history(self) -> _History:
        if self._history is not None:
            return self._history
        needed = {k: f for k, f in DATA_FILES.items() if self._injected[k] is None}
        frames: dict[str, pd.DataFrame] = {k: v for k, v in self._injected.items() if v is not None}
        sources = ["injected"] if frames else []
        if needed:
            processed = self._read_dir(self.data_dir / "processed", needed)
            if processed is not None:
                frames.update(processed)
                sources.append("processed")
            else:
                self._processed_missing = True
                sample = self._read_dir(
                    self.data_dir / "sample",
                    {k: f.replace(".parquet", "_sample.parquet") for k, f in needed.items()},
                )
                if sample is None:
                    raise ForecastUnavailableError(
                        f"no forecast data: {self.data_dir / 'processed'} and {self.data_dir / 'sample'} "
                        f"are both missing {sorted(needed.values())}"
                    )
                frames.update(sample)
                sources.append("sample")

        tz = site_tz(self.config)
        weather = _indexed(frames["weather"], tz)
        pv = _indexed(frames["pv"], tz)["pv_kw"].astype(float).dropna()
        load = _indexed(frames["load"], tz)["base_kw"].astype(float).dropna()
        weather_cols = [c for c in solar_model.FEATURE_COLS if c in weather.columns]
        weather_ok = weather.dropna(subset=weather_cols)
        hours = weather_ok.index.floor("h").unique()
        slots_per_day = 24 * 60 // 15
        covered = (
            _full_days(hours, 24)
            & _full_days(pv.index, slots_per_day)
            & _full_days(load.index, slots_per_day)
        )
        self._history = _History(weather, pv, load, "+".join(sources), covered)
        return self._history

    # ------------------------------------------------------------------ tier 0: model

    def _model_blockers(self, local: datetime, hist: _History) -> tuple[list[str], list[str]]:
        """Reasons (codes + details) why the trained models cannot be used for ``local``."""
        reasons: list[str] = []
        details: list[str] = []
        if (local.hour, local.minute) != (0, 0):
            reasons.append(REASON_NON_MIDNIGHT)
            details.append(f"start {local.isoformat()} is not 00:00; models forecast whole days only")
        if (
            slot_minutes(self.config) != 15
            or n_slots(self.config) != 96
            or str(site_tz(self.config)) != str(solar_model.DHAKA_TZ)
        ):
            reasons.append(REASON_AXIS)
            details.append("models need 96 x 15-min slots in Asia/Dhaka")
        day = local.date()
        if day not in hist.covered:
            lo, hi = (min(hist.covered), max(hist.covered)) if hist.covered else (None, None)
            if lo is None or day < lo or day > hi:
                reasons.append(REASON_OUT_OF_RANGE)
                details.append(f"{day.isoformat()} outside data range {hist.data_range}")
            else:
                reasons.append(REASON_HISTORY)
                details.append(f"{day.isoformat()} has gaps in weather/pv/load")
        else:
            missing = [day - timedelta(days=k) for k in range(1, LAG_DAYS + 1)]
            missing = [d for d in missing if d not in hist.covered]
            if missing:
                reasons.append(REASON_HISTORY)
                details.append("lag days missing: " + ",".join(d.isoformat() for d in missing))
        absent = [f for f in MODEL_FILES if not (self.models_dir / f).exists()]
        if absent:
            reasons.append(REASON_MODELS)
            details.append(f"missing in {self.models_dir}: " + ",".join(absent))
        return reasons, details

    def _model_forecast(self, local: datetime, hist: _History) -> Forecast:
        # Slice to the target day + lag window: faster, and the inputs are known to be gap-free here.
        lo = pd.Timestamp(local) - pd.Timedelta(days=LAG_DAYS)
        hi = pd.Timestamp(local) + pd.Timedelta(days=1)
        weather = hist.weather.loc[lo:hi].reset_index()
        pv = hist.pv.loc[lo : hi - pd.Timedelta(minutes=1)].rename("pv_kw").reset_index()
        load = hist.load.loc[lo : hi - pd.Timedelta(minutes=1)].rename("base_kw").reset_index()
        solar_df = solar_model.predict(local, weather_df=weather, pv_df=pv, models_dir=self.models_dir)
        load_df = load_model.predict(local, load_df=load, weather_df=weather, models_dir=self.models_dir)
        solar_q = self._series_from_frame(solar_df, local, "solar")
        load_q = self._series_from_frame(load_df, local, "load")
        return Forecast(start=local, solar=solar_q, load=load_q)

    def _series_from_frame(self, df: pd.DataFrame, local: datetime, name: str) -> QuantileSeries:
        expected = slot_times(local, self.config)
        if len(df) != len(expected):
            raise _InvalidOutput(f"{name} predict returned {len(df)} rows, expected {len(expected)}")
        got = pd.DatetimeIndex(df["timestamp"])
        if not (got == pd.DatetimeIndex(expected)).all():
            raise _InvalidOutput(f"{name} predict timestamps do not match the slots from {local.isoformat()}")
        try:
            p10, p50, p90 = repair_quantiles(df["p10_kw"], df["p50_kw"], df["p90_kw"])
        except _InvalidOutput as exc:
            raise _InvalidOutput(f"{name}: {exc}") from exc
        return QuantileSeries(p10_kw=p10, p50_kw=p50, p90_kw=p90)

    # ------------------------------------------------------------------ tiers 1-2: persistence

    def _persistence_forecast(
        self, local: datetime, hist: _History, reasons: list[str], details: list[str]
    ) -> Forecast:
        slots = pd.DatetimeIndex(slot_times(local, self.config))
        day = local.date()
        solar_from, solar_recent = self._pick_source(
            hist.pv, slots, day, SOLAR_RECENT_DELTAS, same_weekday=False
        )
        load_from, load_recent = self._pick_source(
            hist.load, slots, day, LOAD_RECENT_DELTAS, same_weekday=True
        )
        solar_q = self._band(hist.pv, slots, day, solar_from, step_days=1, count=SOLAR_BAND_DAYS)
        load_q = self._band(hist.load, slots, day, load_from, step_days=7, count=LOAD_BAND_WEEKS)
        note = format_note(
            {
                "reason": ",".join(reasons),
                "method": "persistence" if solar_recent and load_recent else "analog_day",
                "source": hist.source,
                "solar_from": solar_from.isoformat(),
                "load_from": load_from.isoformat(),
                "data_range": hist.data_range,
                "detail": " | ".join(details),
            }
        )
        logger.warning("%s", note)
        return Forecast(start=local, solar=solar_q, load=load_q, fallback_used=True, note=note)

    @staticmethod
    def _has(series: pd.Series, slots: pd.DatetimeIndex, day: date, source: date) -> bool:
        shifted = slots - pd.Timedelta(days=(day - source).days)
        return bool(shifted.isin(series.index).all())

    def _pick_source(
        self,
        series: pd.Series,
        slots: pd.DatetimeIndex,
        day: date,
        recent: tuple[int, ...],
        *,
        same_weekday: bool,
    ) -> tuple[date, bool]:
        """Source day whose slots are copied, and whether it is a recent (persistence) day.

        Order: ``day - recent[i]``; same month-day in each year of the data, latest first (moved to the
        nearest same weekday when ``same_weekday``); latest available day (same weekday first).
        """
        ok: Callable[[date], bool] = lambda d: self._has(series, slots, day, d)  # noqa: E731
        for delta in recent:
            if ok(day - timedelta(days=delta)):
                return day - timedelta(days=delta), True
        days = sorted(set(series.index.date), reverse=True)
        for year in sorted({d.year for d in days}, reverse=True):
            try:
                analog = day.replace(year=year)
            except ValueError:  # 29 Feb in a non-leap year
                analog = day.replace(year=year, day=28)
            if same_weekday:
                shift = (day.weekday() - analog.weekday() + 3) % 7 - 3  # -3..3 to the same weekday
                analog += timedelta(days=shift)
            if analog != day and ok(analog):
                return analog, False
        candidates = [d for d in days if d != day]
        if same_weekday:
            candidates = [d for d in candidates if d.weekday() == day.weekday()] + [
                d for d in candidates if d.weekday() != day.weekday()
            ]
        for d in candidates:
            if ok(d):
                return d, False
        raise ForecastUnavailableError(
            f"no complete {len(slots)}-slot window in the history to copy for {day.isoformat()}"
        )

    def _band(
        self,
        series: pd.Series,
        slots: pd.DatetimeIndex,
        day: date,
        source: date,
        *,
        step_days: int,
        count: int,
    ) -> QuantileSeries:
        """P50 = the source day; P10/P90 = per-slot 10th/90th percentiles over up to ``count`` days
        ``source, source - step, ...`` that exist, widened to contain P50."""
        rows = []
        for k in range(count):
            d = source - timedelta(days=k * step_days)
            if d == day or not self._has(series, slots, day, d):
                continue
            shifted = slots - pd.Timedelta(days=(day - d).days)
            rows.append(series.reindex(shifted).to_numpy(dtype=float))
        mat = np.vstack(rows)  # rows[0] is the source day itself (checked by _pick_source)
        p50 = mat[0]
        p10 = np.minimum(np.percentile(mat, 10, axis=0), p50)
        p90 = np.maximum(np.percentile(mat, 90, axis=0), p50)
        a, b, c = repair_quantiles(p10, p50, p90)
        return QuantileSeries(p10_kw=a, p50_kw=b, p90_kw=c)
