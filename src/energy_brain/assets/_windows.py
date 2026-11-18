"""Wall-clock window helpers shared by the asset agents (#16).

Windows are ``[start, end)`` in local (Asia/Dhaka) clock time and may wrap midnight, e.g. 18:00-07:00.
``start == end`` means a full 24 h window.
"""

from __future__ import annotations

from datetime import time

MINUTES_PER_DAY = 24 * 60


def clock_minutes(t: time) -> int:
    """Minutes since local midnight."""
    return t.hour * 60 + t.minute


def in_window(minute: int, start: time, end: time) -> bool:
    """True if clock ``minute`` (0-1439) lies in ``[start, end)``, handling windows that wrap midnight."""
    a, b = clock_minutes(start), clock_minutes(end)
    if a == b:
        return True
    return a <= minute < b if a < b else (minute >= a or minute < b)


def slot_overlaps(minute: int, slot_minutes: int, start: time, end: time) -> bool:
    """True if the slot ``[minute, minute + slot_minutes)`` overlaps ``[start, end)`` at all."""
    if in_window(minute, start, end):
        return True
    # The slot starts outside the window, so they overlap only if the window opens inside the slot.
    return (clock_minutes(start) - minute) % MINUTES_PER_DAY < slot_minutes
