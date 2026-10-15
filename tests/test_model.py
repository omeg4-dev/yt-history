from datetime import date

from ythist.model import Entry, parse_duration, parse_views, resolve_date

TODAY = date(2026, 10, 15)  # a Thursday


def test_relative_dates():
    assert resolve_date("Today", TODAY) == TODAY
    assert resolve_date("Yesterday", TODAY) == date(2026, 10, 14)
    assert resolve_date("Monday", TODAY) == date(2026, 10, 12)
    assert resolve_date("Thursday", TODAY) == date(2026, 10, 8)  # same weekday = a week ago


def test_absolute_dates():
    assert resolve_date("Sep 3", TODAY) == date(2026, 9, 3)
    assert resolve_date("Sept 3", TODAY) == date(2026, 9, 3)
    assert resolve_date("Dec 30, 2024", TODAY) == date(2024, 12, 30)
    assert resolve_date("Dec 30", date(2026, 1, 2)) == date(2025, 12, 30)
    assert resolve_date("Shorts", TODAY) is None


def test_views():
    assert parse_views("1.2M views") == 1_200_000
    assert parse_views("12,345 views") == 12_345
    assert parse_views("987 views") == 987
    assert parse_views("3.4K views") == 3_400
    assert parse_views("1 view") == 1
    assert parse_views("No views") == 0
    assert parse_views("1M") == 1_000_000  # history lockups drop the word "views"
    assert parse_views("581K") == 581_000
    assert parse_views("2 days ago") is None
    assert parse_views(None) is None


def test_duration():
    assert parse_duration("4:05") == 245
    assert parse_duration("1:02:03") == 3723
    assert parse_duration("LIVE") is None
    assert parse_duration(None) is None


def test_watched_seconds():
    e = Entry("x", "t", "2026-10-15", "Today", duration_s=600, percent=50)
    assert e.watched_s == 300
    assert Entry("x", "t", "d", "l", duration_s=600).watched_s is None
    assert Entry("x", "t", "d", "l", duration_s=40, is_short=True).watched_s == 40
