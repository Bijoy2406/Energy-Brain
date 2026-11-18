"""Signal agent (issue #15): tariff, grid CO2, diesel price/CO2 and outage flags per slot.

``CalendarSignalAgent.run(start) -> SignalTable`` satisfies ``energy_brain.interfaces.SignalAgent``.
See ``docs/interfaces.md`` section "Signal agent rules".

Per slot ``t`` (slot start ``s_t = start + t * slot_minutes()``):

* ``tariff_bdt_per_kwh``: BERC MT-3 time-of-use rate at ``s_t`` from ``config/signals.yaml`` via
  :func:`energy_brain.data.signals_lookup.get_tariff_for_datetime` (peak 17-23, off-peak 23-06, else flat).
  Every window edge is a whole hour, so a slot never straddles two windows when ``slot_minutes`` divides 60;
  a straddling slot raises ``ValueError`` instead of silently picking one rate.
* ``grid_co2_kg_per_kwh``: flat national factor ``grid_emission.factor_kg_per_kwh`` (0.62 kg/kWh,
  Department of Environment / UNFCCC BUR1, as of 2022). No hourly grid-carbon data exists in the repo;
  none is invented.
* ``diesel_price_bdt_per_l`` / ``diesel_co2_kg_per_l``: flat ``diesel.price_bdt_per_l`` (107 BDT/L, BPC, as of
  2024-05) and ``diesel.emission_factor_kg_per_l`` (2.68 kg/L, IPCC 2006 / DEFRA).
* ``outage``: True when any outage event overlaps any part of the slot (partial overlap rounds up), the same
  rule as :func:`energy_brain.data.outages.get_daily_outage_mask`. The Planner forces grid import to 0 there.

Outage events, first source that works (cached after the first ``run``):

1. ``outages_df`` passed to the constructor (``source=injected``);
2. ``data/processed/outages.parquet`` (``source=processed``);
3. :func:`energy_brain.data.outages.generate_outage_events` in memory with its default seed and range
   (``source=generated``), i.e. the same calendar the processed file is built from. CI has no processed data.

The calendar covers ``data_range`` (default 2022-01-01..2024-12-31). Each calendar day the planning window
touches is resolved on its own: a day inside the range uses its own events; a day outside uses the **analog
day** (the same calendar day in the latest year of the range, moved to the nearest same weekday, the rule the
Forecast agent uses for load) with its events shifted onto the target day. Any analog day gives a fallback
note in the stable one-line format built by :func:`format_note` and read by :func:`parse_note`::

    SIGNAL FALLBACK: reason=out_of_range; method=analog_day; source=<injected|processed|generated>;
    outage_from=<YYYY-MM-DD>[,<YYYY-MM-DD>]; data_range=<YYYY-MM-DD..YYYY-MM-DD>; detail=<text>

``SignalTable`` has no note field, so the note is returned by :meth:`CalendarSignalAgent.run_with_note`, kept
in ``CalendarSignalAgent.last_note`` and logged as a warning. A non-midnight ``start`` is fully supported (the
window may span two calendar days) and is not a fallback.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

from energy_brain.config import SiteConfig, load_site_config
from energy_brain.data.outages import generate_outage_events
from energy_brain.data.signals_lookup import SignalsConfig, get_tariff_for_datetime, load_signals_config
from energy_brain.schemas import SignalTable
from energy_brain.timeaxis import ensure_start, n_slots, site_tz, slot_minutes, slot_times

logger = logging.getLogger(__name__)

DEFAULT_ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_DATA_DIR = DEFAULT_ROOT_DIR / "data"
OUTAGES_FILE = "outages.parquet"

#: Days covered by the synthetic outage calendar (``generate_outage_events`` defaults), inclusive.
DEFAULT_DATA_RANGE = (date(2022, 1, 1), date(2024, 12, 31))

#: First words of every fallback note.
NOTE_PREFIX = "SIGNAL FALLBACK:"
#: Keys of a fallback note, in order. ``detail`` is always last and is free text.
NOTE_KEYS = ("reason", "method", "source", "outage_from", "data_range", "detail")

# Reason codes (stable; the Explainer may quote them).
REASON_OUT_OF_RANGE = "out_of_range"  # a day of the window is outside data_range: analog day used

SOURCE_INJECTED = "injected"
SOURCE_PROCESSED = "processed"
SOURCE_GENERATED = "generated"


def format_note(fields: dict[str, str]) -> str:
    """Build a fallback note from ``fields`` (keys from ``NOTE_KEYS``) in the stable order."""
    parts = []
    for key in NOTE_KEYS:
        value = str(fields.get(key, "")).replace(";", ",").replace("\n", " ").strip()
        parts.append(f"{key}={value}")
    return f"{NOTE_PREFIX} " + "; ".join(parts)


def parse_note(note: str | None) -> dict[str, str] | None:
    """Inverse of :func:`format_note`: dict of note fields, or None if ``note`` is not a signal note."""
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


def analog_day(day: date, data_range: tuple[date, date]) -> date:
    """Day inside ``data_range`` standing in for ``day``: same month-day in the latest year of the range that
    works, moved to the nearest same weekday (-3..+3 days); else the latest same weekday in the range."""
    first, last = data_range
    for year in range(last.year, first.year - 1, -1):
        try:
            analog = day.replace(year=year)
        except ValueError:  # 29 Feb in a non-leap year
            analog = day.replace(year=year, day=28)
        analog += timedelta(days=(day.weekday() - analog.weekday() + 3) % 7 - 3)
        if first <= analog <= last:
            return analog
    analog = last - timedelta(days=(last.weekday() - day.weekday()) % 7)
    if analog < first:
        raise ValueError(f"data_range {first}..{last} has no {day:%A}")
    return analog


def outage_mask(
    times: list[datetime], step: timedelta, events: list[tuple[datetime, datetime]]
) -> list[bool]:
    """True for each slot ``[t, t + step)`` overlapping an event ``[start, end)`` (partial overlap counts)."""
    return [any(max(t, s) < min(t + step, e) for s, e in events) for t in times]


class CalendarSignalAgent:
    """Signal agent (#15): BERC tariff, flat grid CO2, diesel price/CO2 and the outage calendar per slot.

    ``outages_df`` (columns ``start``, ``end``) replaces the outage files; ``signals_config`` replaces
    ``config/signals.yaml``; ``data_range`` is the inclusive span of days the outage calendar covers (a day
    without events inside it means "no outage"). Nothing is read until the first ``run``; the config and the
    calendar are then cached.
    """

    def __init__(
        self,
        *,
        outages_df: pd.DataFrame | None = None,
        signals_config: SignalsConfig | Path | None = None,
        data_dir: Path | None = None,
        data_range: tuple[date, date] = DEFAULT_DATA_RANGE,
        config: SiteConfig | None = None,
    ) -> None:
        if data_range[0] > data_range[1]:
            raise ValueError(f"data_range start {data_range[0]} is after its end {data_range[1]}")
        self._injected_outages = outages_df
        self._signals_arg = signals_config
        self.data_dir = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
        self.data_range = data_range
        self.config = config if config is not None else load_site_config()
        self._signals: SignalsConfig | None = None
        self._events: pd.DataFrame | None = None
        self.outage_source: str | None = None
        #: Fallback note of the most recent ``run`` (None when every day came from the calendar itself).
        self.last_note: str | None = None

    # ------------------------------------------------------------------ public

    def run(self, start: datetime) -> SignalTable:
        """Signal table for the ``n_slots()`` slots from ``start``; the note goes to ``last_note``."""
        table, _ = self.run_with_note(start)
        return table

    def run_with_note(self, start: datetime) -> tuple[SignalTable, str | None]:
        """Signal table plus the fallback note (None unless an analog day was used).

        Raises ``ValueError`` for a naive or off-slot ``start`` (programmer error).
        """
        local = ensure_start(start, self.config)
        sig = self._load_signals()
        times = slot_times(local, self.config)
        step = timedelta(minutes=slot_minutes(self.config))
        n = n_slots(self.config)

        tariff = [self._slot_tariff(t, step, sig) for t in times]
        events, analogs = self._window_events(times[0], times[-1] + step)
        table = SignalTable(
            start=local,
            tariff_bdt_per_kwh=tariff,
            grid_co2_kg_per_kwh=[sig.grid_emission.factor_kg_per_kwh] * n,
            diesel_price_bdt_per_l=[sig.diesel.price_bdt_per_l] * n,
            diesel_co2_kg_per_l=[sig.diesel.emission_factor_kg_per_l] * n,
            outage=outage_mask(times, step, events),
        )

        note = None
        if analogs:
            first, last = self.data_range
            note = format_note(
                {
                    "reason": REASON_OUT_OF_RANGE,
                    "method": "analog_day",
                    "source": self.outage_source or "",
                    "outage_from": ",".join(a.isoformat() for _, a in analogs),
                    "data_range": f"{first.isoformat()}..{last.isoformat()}",
                    "detail": ", ".join(
                        f"{d.isoformat()} outside outage calendar, outages copied from {a.isoformat()}"
                        for d, a in analogs
                    ),
                }
            )
            logger.warning("%s", note)
        self.last_note = note
        return table, note

    def provenance(self) -> str:
        """One line naming every source and as-of date (for the Data and AI provenance document)."""
        sig = self._load_signals()
        self._load_events()
        t, g, d = sig.tariff, sig.grid_emission, sig.diesel
        first, last = self.data_range
        return (
            f"SIGNAL SOURCES: tariff={t.regulation} {t.customer_class} ({t.source}) as_of={t.as_of}; "
            f"grid_co2={g.factor_kg_per_kwh} kg/kWh flat ({g.authority}, {g.report}) as_of={g.as_of}; "
            f"diesel_price={d.price_bdt_per_l} BDT/L ({d.price_authority}) as_of={d.as_of}; "
            f"diesel_co2={d.emission_factor_kg_per_l} kg/L ({d.emission_source}); "
            f"outages=synthetic seeded calendar source={self.outage_source} "
            f"data_range={first.isoformat()}..{last.isoformat()}"
        )

    # ------------------------------------------------------------------ inputs (lazy, cached)

    def _load_signals(self) -> SignalsConfig:
        if self._signals is None:
            arg = self._signals_arg
            self._signals = arg if isinstance(arg, SignalsConfig) else load_signals_config(arg)
        return self._signals

    def _load_events(self) -> pd.DataFrame:
        if self._events is not None:
            return self._events
        if self._injected_outages is not None:
            raw, source = self._injected_outages, SOURCE_INJECTED
        else:
            raw, source = None, SOURCE_GENERATED
            path = self.data_dir / "processed" / OUTAGES_FILE
            if path.exists():
                try:
                    raw, source = pd.read_parquet(path), SOURCE_PROCESSED
                except Exception as exc:
                    logger.warning("cannot read %s (%s); regenerating the outage calendar", path, exc)
            if raw is None:
                raw = generate_outage_events()
        self._events = self._normalise(raw)
        self.outage_source = source
        return self._events

    def _normalise(self, raw: pd.DataFrame) -> pd.DataFrame:
        tz = site_tz(self.config)
        out = pd.DataFrame(columns=["start", "end"])
        if len(raw):
            cols = {}
            for col in ("start", "end"):
                ts = pd.to_datetime(raw[col])
                cols[col] = ts.dt.tz_localize(tz) if ts.dt.tz is None else ts.dt.tz_convert(tz)
            out = pd.DataFrame(cols)
            out = out[out["end"] > out["start"]].sort_values("start").reset_index(drop=True)
        return out

    # ------------------------------------------------------------------ per-slot values

    @staticmethod
    def _slot_tariff(t: datetime, step: timedelta, sig: SignalsConfig) -> float:
        rate = get_tariff_for_datetime(t, sig)
        if get_tariff_for_datetime(t + step - timedelta(microseconds=1), sig) != rate:
            raise ValueError(f"slot starting {t.isoformat()} straddles a tariff window edge")
        return rate

    def _window_events(
        self, window_start: datetime, window_end: datetime
    ) -> tuple[list[tuple[datetime, datetime]], list[tuple[date, date]]]:
        """Outage intervals for ``[window_start, window_end)``, resolved per calendar day, and the
        ``(day, analog_day)`` pairs used for days outside ``data_range``."""
        df = self._load_events()
        tz = site_tz(self.config)
        first, last = self.data_range
        events: list[tuple[datetime, datetime]] = []
        analogs: list[tuple[date, date]] = []
        day = window_start.date()
        while datetime.combine(day, time(), tz) < window_end:
            source = day
            if not first <= day <= last:
                source = analog_day(day, self.data_range)
                analogs.append((day, source))
            lo = datetime.combine(source, time(), tz)
            hi = lo + timedelta(days=1)
            shift = timedelta(days=(day - source).days)
            hits = df[(df["end"] > lo) & (df["start"] < hi)]
            for s, e in zip(hits["start"], hits["end"], strict=True):
                s, e = max(s.to_pydatetime(), lo), min(e.to_pydatetime(), hi)  # clip to the source day
                events.append((s + shift, e + shift))
            day += timedelta(days=1)
        return events, analogs
