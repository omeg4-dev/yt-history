"""Small formatting helpers shared by the dashboard and the stats report."""

from __future__ import annotations


def dur(seconds: float | None, *, short: bool = False) -> str:
    """Human duration: '3d 4h', '2h 05m', '7m 12s'."""
    if seconds is None:
        return "–"
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}d {h}h" if short else f"{d}d {h}h {m:02d}m"
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s" if not short else f"{m}m"
    return f"{s}s"


def clock(seconds: int | None) -> str:
    if seconds is None:
        return "–"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def num(n: float | None) -> str:
    """Compact count: 1.2M, 34K, 999."""
    if n is None:
        return "–"
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(n) >= div:
            v = n / div
            return f"{v:.1f}{suf}" if v < 100 else f"{v:.0f}{suf}"
    return f"{int(n)}"


BLOCKS = " ▏▎▍▌▋▊▉█"
SPARK = "▁▂▃▄▅▆▇█"


def bar(frac: float, width: int) -> str:
    """Smooth horizontal bar using eighth blocks."""
    frac = max(0.0, min(1.0, frac))
    full, rem = divmod(round(frac * width * 8), 8)
    out = "█" * full + (BLOCKS[rem] if rem and full < width else "")
    return out.ljust(width)


def spark(values: list[float]) -> str:
    if not values:
        return ""
    hi = max(values) or 1
    return "".join(SPARK[min(7, int(v / hi * 7.999))] if v else " " for v in values)


def pct_color(p: float | None) -> str:
    if p is None:
        return "grey50"
    if p >= 90:
        return "green3"
    if p >= 50:
        return "yellow3"
    if p >= 20:
        return "dark_orange"
    return "red3"
