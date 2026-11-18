"""Signal agent (#15): tariff, grid CO2, diesel cost/CO2 and outage flags per 15-minute slot."""

from energy_brain.signals.agent import (
    NOTE_PREFIX,
    CalendarSignalAgent,
    analog_day,
    format_note,
    parse_note,
)

__all__ = ["NOTE_PREFIX", "CalendarSignalAgent", "analog_day", "format_note", "parse_note"]
