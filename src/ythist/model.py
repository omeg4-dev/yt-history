"""Data model plus the small text parsers (dates, view counts, durations)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass
class Entry:
    video_id: str
    title: str
    watch_date: str              # ISO date
    date_label: str
    position: int = 0
    channel_name: str | None = None
    channel_id: str | None = None
    channel_handle: str | None = None
    duration_s: int | None = None
    views: int | None = None
    percent: float | None = None
    is_short: bool = False

    @property
    def watched_s(self) -> int | None:
        if self.duration_s is None:
            return None
        if self.percent is None:
            # No progress bar: Shorts loop and never get one -> count as fully watched.
            return self.duration_s if self.is_short else None
        return round(self.duration_s * self.percent / 100)


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def resolve_date(label: str, today: date | None = None) -> date | None:
    """Turn a history section header into a date.

    YouTube uses "Today", "Yesterday", a weekday name for the last week,
    "Sep 3" for earlier this year and "Sep 3, 2024" for older years.
    """
    today = today or date.today()
    s = label.strip().lower()
    if s == "today":
        return today
    if s == "yesterday":
        return today - timedelta(days=1)
    if s in WEEKDAYS:
        back = (today.weekday() - WEEKDAYS.index(s)) % 7 or 7
        return today - timedelta(days=back)
    m = re.fullmatch(r"([a-z]{3})[a-z]*\.?\s+(\d{1,2})(?:,\s*(\d{4}))?", s)
    if m and m.group(1) in MONTHS:
        year = int(m.group(3)) if m.group(3) else today.year
        d = date(year, MONTHS[m.group(1)], int(m.group(2)))
        if not m.group(3) and d > today:  # "Dec 30" seen in January
            d = d.replace(year=year - 1)
        return d
    return None


_SUFFIX = {"k": 1e3, "m": 1e6, "b": 1e9}


def parse_views(text: str | None) -> int | None:
    """'1.2M views' / '1.2M' -> 1200000, '12,345 views' -> 12345, 'No views' -> 0."""
    if not text:
        return None
    s = text.strip().lower().replace(",", "")
    if s.startswith("no view"):
        return 0
    m = re.fullmatch(r"([\d.]+)\s*([kmb])?(\s+views?)?", s)
    if not m:
        return None
    return round(float(m.group(1)) * _SUFFIX.get(m.group(2) or "", 1))


def parse_duration(text: str | None) -> int | None:
    """'1:02:03' -> 3723, '4:05' -> 245. Non-durations ('LIVE', 'SHORTS') -> None."""
    if not text or not re.fullmatch(r"\d+(:\d{1,2}){1,2}", text.strip()):
        return None
    secs = 0
    for part in text.strip().split(":"):
        secs = secs * 60 + int(part)
    return secs


def iso(d: date | datetime) -> str:
    return d.isoformat()[:10]
