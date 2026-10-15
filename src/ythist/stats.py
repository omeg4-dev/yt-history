"""The statistics report, rendered with rich."""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import median

from rich.columns import Columns
from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .fmt import bar, clock, dur, num, pct_color

ACCENT = "#ff3d3d"
HEAT = ["grey23", "#5c1a1a", "#8f2222", "#c42b2b", "#ff3d3d"]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
LEN_BUCKETS = [(0, 60, "< 1 min"), (60, 300, "1–5 min"), (300, 600, "5–10 min"), (600, 1200, "10–20 min"),
               (1200, 2400, "20–40 min"), (2400, 3600, "40–60 min"), (3600, 10**9, "1 h +")]
VIEW_BUCKETS = [(0, 10_000, "niche  < 10K"), (10_000, 100_000, "small  10K–100K"),
                (100_000, 1_000_000, "mid    100K–1M"), (1_000_000, 10_000_000, "big    1M–10M"),
                (10_000_000, 10**13, "viral  10M +")]


def panel(body, title: str, **kw) -> Panel:
    return Panel(body, title=f"[bold]{title}", title_align="left", border_style="grey35", padding=(0, 1), **kw)


def load(con: sqlite3.Connection, since: str | None) -> list[dict]:
    q = "SELECT * FROM history"
    args: tuple = ()
    if since:
        q += " WHERE watch_date >= ?"
        args = (since,)
    return [dict(r) for r in con.execute(q + " ORDER BY watch_date, position DESC", args)]


def streaks(days: list[date]) -> tuple[int, date | None]:
    best, cur, best_end = 0, 0, None
    prev = None
    for d in sorted(set(days)):
        cur = cur + 1 if prev and d - prev == timedelta(days=1) else 1
        if cur > best:
            best, best_end = cur, d
        prev = d
    return best, best_end


# ---------------------------------------------------------------- sections

def overview(rows: list[dict]) -> Panel:
    vids = [r for r in rows if not r["is_short"]]
    shorts = len(rows) - len(vids)
    watched = sum(r["watched_s"] or 0 for r in vids)
    runtime = sum(r["duration_s"] or 0 for r in vids if r["watched_s"] is not None)
    days = [date.fromisoformat(r["watch_date"]) for r in rows]
    first, last = min(days), max(days)
    span = (last - first).days + 1
    active = len(set(days))
    streak, streak_end = streaks(days)
    channels = {r["channel_name"] for r in rows if r["channel_name"]}

    def tile(value: str, label: str, style: str = "bold white") -> Panel:
        return Panel(Group(Text(value, style=style, justify="center"), Text(label, style="grey58", justify="center")),
                     border_style="grey27", width=22, padding=(0, 0))

    tiles = [
        tile(f"{len(rows):,}", "history entries", f"bold {ACCENT}"),
        tile(f"{len({r['video_id'] for r in rows}):,}", "unique videos"),
        tile(f"{len(channels):,}", "channels"),
        tile(f"{shorts:,}", "shorts", "bold magenta"),
        tile(dur(watched, short=True), "watched (progress)", "bold green3"),
        tile(dur(runtime, short=True), "runtime clicked on"),
        tile(f"{watched / runtime:.0%}" if runtime else "–", "watched of runtime", "bold yellow3"),
        tile(dur(watched / active if active else 0, short=True), "per active day"),
        tile(f"{active:,}/{span:,}", "active days"),
        tile(f"{streak} days", f"best streak → {streak_end:%b %d}" if streak_end else "best streak"),
    ]
    sub = Text(f"{first:%d %b %Y} → {last:%d %b %Y}  ·  "
               f"if you watched it back-to-back: {watched / 86400:.1f} days non-stop", style="grey62")
    return panel(Group(Columns(tiles, padding=(0, 1)), sub), "overview")


def top_channels(rows: list[dict], top: int) -> Panel:
    by = defaultdict(lambda: {"n": 0, "t": 0, "pcts": [], "days": set()})
    for r in rows:
        if not r["channel_name"]:
            continue
        c = by[r["channel_name"]]
        c["n"] += 1
        c["t"] += r["watched_s"] or 0
        c["days"].add(r["watch_date"])
        if r["percent"] is not None:
            c["pcts"].append(r["percent"])
    total = sum(c["t"] for c in by.values()) or 1
    ranked = sorted(by.items(), key=lambda kv: kv[1]["t"], reverse=True)[:top]
    hi = ranked[0][1]["t"] if ranked else 1

    t = Table(box=None, expand=True, header_style="grey50", padding=(0, 1))
    t.add_column("#", justify="right", style="grey50", width=3)
    t.add_column("channel", style="cyan", no_wrap=True, ratio=3)
    t.add_column("time watched", ratio=4, no_wrap=True)
    t.add_column("share", justify="right", width=6)
    t.add_column("videos", justify="right", width=6)
    t.add_column("days", justify="right", width=5, style="grey62")
    t.add_column("avg done", justify="right", width=8)
    for i, (name, c) in enumerate(ranked, 1):
        avg = sum(c["pcts"]) / len(c["pcts"]) if c["pcts"] else None
        t.add_row(str(i), name, Text(bar(c["t"] / hi, 22), style=ACCENT) + Text(f" {dur(c['t'], short=True)}", style="white"),
                  f"{c['t'] / total:.1%}", str(c["n"]), str(len(c["days"])),
                  Text(f"{avg:.0f}%" if avg is not None else "–", style=pct_color(avg)))
    top5 = sum(c["t"] for _, c in ranked[:5]) / total
    note = Text(f"top 5 channels = {top5:.0%} of your watch time  ·  "
                f"{sum(1 for c in by.values() if c['n'] == 1):,} channels you only watched once", style="grey62")
    return panel(Group(t, Text(""), note), f"top {len(ranked)} channels by time watched")


def calendar(rows: list[dict]) -> Panel:
    per_day = Counter()
    for r in rows:
        per_day[r["watch_date"]] += r["watched_s"] or (30 if r["is_short"] else 0)
    last = max(date.fromisoformat(d) for d in per_day)
    first = max(min(date.fromisoformat(d) for d in per_day), last - timedelta(weeks=52))
    start = first - timedelta(days=first.weekday())
    weeks = (last - start).days // 7 + 1
    vals = sorted(v for v in per_day.values() if v)
    cuts = [vals[int(len(vals) * q)] for q in (0.25, 0.5, 0.75)] if vals else [1, 2, 3]

    def level(v: int) -> int:
        return 0 if not v else 1 + sum(v > c for c in cuts)

    head = [" "] * (weeks * 2 + 3)
    prev_month = None
    for w in range(weeks):
        d = start + timedelta(weeks=w)
        if d.month != prev_month and all(c == " " for c in head[max(0, w * 2 - 1):w * 2 + 3]):
            head[w * 2:w * 2 + 3] = f"{d:%b}"
            prev_month = d.month
    lines = [Text("     " + "".join(head).rstrip(), style="grey58")]
    for wd in range(7):
        line = Text(f"{WEEKDAYS[wd]}  ", style="grey50")
        for w in range(weeks):
            d = start + timedelta(weeks=w, days=wd)
            if d < first or d > last:
                line.append("  ")
            else:
                line.append("■ ", style=HEAT[level(per_day.get(d.isoformat(), 0))])
        lines.append(line)
    legend = Text("     less ", style="grey50")
    for c in HEAT:
        legend.append("■ ", style=c)
    legend.append("more   ", style="grey50")
    legend.append(f"(watch time per day; quartiles {dur(cuts[0], short=True)} / {dur(cuts[1], short=True)} / "
                  f"{dur(cuts[2], short=True)})", style="grey42")
    lines += [Text(""), legend]
    return panel(Group(*lines), "calendar")


def rhythm(rows: list[dict]) -> Columns:
    # weekday: average watch time on that weekday across the span
    by_day = Counter()
    for r in rows:
        by_day[r["watch_date"]] += r["watched_s"] or 0
    first = min(date.fromisoformat(d) for d in by_day)
    last = max(date.fromisoformat(d) for d in by_day)
    wd_total, wd_count = Counter(), Counter()
    d = first
    while d <= last:
        wd_total[d.weekday()] += by_day.get(d.isoformat(), 0)
        wd_count[d.weekday()] += 1
        d += timedelta(days=1)
    avgs = [wd_total[i] / wd_count[i] if wd_count[i] else 0 for i in range(7)]
    hi = max(avgs) or 1
    wt = Table.grid(padding=(0, 1))
    for i, a in enumerate(avgs):
        style = ACCENT if a == hi else "grey70"
        wt.add_row(Text(WEEKDAYS[i], style="grey62"), Text(bar(a / hi, 20), style=style), Text(dur(a, short=True)))

    months = Counter()
    for k, v in by_day.items():
        months[k[:7]] += v
    keys = sorted(months)[-18:]
    mhi = max((months[k] for k in keys), default=1) or 1
    mt = Table.grid(padding=(0, 1))
    for k in keys:
        mt.add_row(Text(date.fromisoformat(k + "-01").strftime("%b %y"), style="grey62"),
                   Text(bar(months[k] / mhi, 24), style=ACCENT if months[k] == mhi else "grey70"),
                   Text(dur(months[k], short=True)))
    return Columns([panel(wt, "avg per weekday"), panel(mt, "per month")], expand=True)


def completion(rows: list[dict]) -> Columns:
    vids = [r for r in rows if not r["is_short"] and r["percent"] is not None]
    # histogram
    # YouTube's bar bottoms out at 10%, so "10" means "opened it and left".
    edges = [(-1, 10, "bounced  ≤ 10%"), (10, 25, "sampled 11–25%"), (25, 50, "half-ish 26–50%"),
             (50, 89, "most    51–89%"), (89, 100, "finished ≥ 90%")]
    counts = [sum(lo < r["percent"] <= hi for r in vids) for lo, hi, _ in edges]
    hi = max(counts) or 1
    h = Table.grid(padding=(0, 1))
    for (lo, _, label), c in zip(edges, counts):
        h.add_row(Text(label, style="grey62"), Text(bar(c / hi, 22), style=pct_color(lo + 1)),
                  Text(f"{c:,}"), Text(f"{c / len(vids):.0%}" if vids else "", style="grey50"))

    # by length
    lt = Table(box=None, header_style="grey50", padding=(0, 1))
    lt.add_column("length")
    lt.add_column("videos", justify="right")
    lt.add_column("avg watched", no_wrap=True)
    lt.add_column("finished", justify="right")
    best = None
    for lo, hi_, label in LEN_BUCKETS:
        b = [r for r in vids if r["duration_s"] is not None and lo <= r["duration_s"] < hi_]
        if not b:
            continue
        avg = sum(r["percent"] for r in b) / len(b)
        fin = sum(r["percent"] >= 90 for r in b) / len(b)
        best = best or (label, avg)
        lt.add_row(label, f"{len(b):,}", Text(bar(avg / 100, 12), style=pct_color(avg)) + Text(f" {avg:.0f}%"),
                   f"{fin:.0%}")
    return Columns([panel(h, "how much you watch"), panel(lt, "by video length")], expand=True)


def popularity(rows: list[dict], top: int) -> Columns:
    vids = {r["video_id"]: r for r in rows if r["views"] is not None}
    vt = Table.grid(padding=(0, 1))
    counts = [sum(lo <= r["views"] < hi for r in vids.values()) for lo, hi, _ in VIEW_BUCKETS]
    hi = max(counts) or 1
    for (_, _, label), c in zip(VIEW_BUCKETS, counts):
        vt.add_row(Text(label, style="grey62"), Text(bar(c / hi, 20), style=ACCENT), Text(f"{c:,}"))
    med = median(r["views"] for r in vids.values()) if vids else 0
    body = Group(vt, Text(""), Text(f"median video you watch: {num(med)} views", style="grey62"))

    gems = sorted((r for r in vids.values() if not r["is_short"] and (r["percent"] or 0) >= 90),
                  key=lambda r: r["views"])[: min(top, 10)]
    gt = Table(box=None, header_style="grey50", padding=(0, 1), expand=True)
    gt.add_column("views", justify="right", width=6)
    gt.add_column("channel", style="cyan", no_wrap=True, ratio=1)
    gt.add_column("title", no_wrap=True, ratio=3)
    for r in gems:
        gt.add_row(num(r["views"]), r["channel_name"] or "?", r["title"])
    return Columns([panel(body, "popularity of what you watch"),
                    panel(gt, "hidden gems · tiny videos you finished")], expand=True)


def highlights(rows: list[dict], top: int) -> Panel:
    vids = [r for r in rows if not r["is_short"]]
    facts: list[tuple[str, str]] = []

    by_day = Counter()
    n_day = Counter()
    for r in rows:
        by_day[r["watch_date"]] += r["watched_s"] or 0
        n_day[r["watch_date"]] += 1
    if n_day:
        d, n = n_day.most_common(1)[0]
        facts.append(("busiest day", f"{n} videos on {date.fromisoformat(d):%a %d %b %Y}"))
    sh = Counter(r["watch_date"] for r in rows if r["is_short"])
    if sh:
        d, c = sh.most_common(1)[0]
        facts.append(("shorts binge", f"{c} shorts on {date.fromisoformat(d):%a %d %b %Y}"))

    finished = [r for r in vids if (r["percent"] or 0) >= 90 and r["watched_s"]]
    if finished:
        r = max(finished, key=lambda r: r["duration_s"])
        facts.append(("longest finished", f"{clock(r['duration_s'])}  {r['title']}  [cyan]({r['channel_name']})"))
    timed = [r for r in vids if r["watched_s"]]
    if timed:
        r = max(timed, key=lambda r: r["watched_s"])
        facts.append(("longest single watch", f"{dur(r['watched_s'])}  {r['title']}  [cyan]({r['channel_name']})"))
    quit_ = [r for r in vids if r["percent"] is not None and r["percent"] <= 10 and r["duration_s"]]
    if quit_:
        facts.append(("bounced", f"{len(quit_):,} videos left within the first 10% — {dur(sum(r['duration_s'] for r in quit_), short=True)} of runtime skipped"))

    ch = defaultdict(list)
    for r in vids:
        if r["channel_name"] and r["percent"] is not None:
            ch[r["channel_name"]].append(r["percent"])
    regulars = {k: sum(v) / len(v) for k, v in ch.items() if len(v) >= 5}
    if regulars:
        k = max(regulars, key=regulars.get)
        facts.append(("most loyal", f"[cyan]{k}[/] — you finish {regulars[k]:.0f}% of each video on average"))
        k = min(regulars, key=regulars.get)
        facts.append(("most skimmed", f"[cyan]{k}[/] — only {regulars[k]:.0f}% on average"))

    # rabbit holes: ≥5 videos of one channel on one day
    holes = Counter((r["watch_date"], r["channel_name"]) for r in vids if r["channel_name"])
    deep = [(k, n) for k, n in holes.most_common() if n >= 5]
    if deep:
        (d, c), n = deep[0]
        facts.append(("deepest rabbit hole", f"{n} videos of [cyan]{c}[/] on {date.fromisoformat(d):%d %b %Y}  "
                                             f"[grey50]({len(deep)} days with 5+ from one channel)"))

    seen = Counter(r["video_id"] for r in rows)
    re = [(vid, n) for vid, n in seen.most_common() if n > 1]
    if re:
        title = next(r["title"] for r in rows if r["video_id"] == re[0][0])
        facts.append(("rewatched", f"{len(re):,} videos on more than one day — top: {title[:70]} ({re[0][1]} days)"))

    t = Table.grid(padding=(0, 2))
    t.add_column(style=f"bold {ACCENT}", justify="right", no_wrap=True, min_width=19)
    t.add_column(overflow="ellipsis", no_wrap=True)
    for k, v in facts:
        t.add_row(k, v)
    return panel(t, "highlights")


def report(con: sqlite3.Connection, console: Console, since: str | None = None, top: int = 15) -> None:
    rows = load(con, since)
    if not rows:
        console.print("[yellow3]no history stored yet — run [bold]ythist scrape[/] first")
        return
    console.print()
    console.print(Rule(f"[bold {ACCENT}]▶ your youtube, by the numbers", style=ACCENT))
    console.print(overview(rows))
    console.print(calendar(rows))
    console.print(top_channels(rows, top))
    console.print(rhythm(rows))
    console.print(completion(rows))
    console.print(popularity(rows, top))
    console.print(highlights(rows, top))
    missing = sum(1 for r in rows if r["is_short"])
    streams = sum(1 for r in rows if (r["duration_s"] or 0) > 43200)
    console.print(Text("  watch time = video length × how far the red progress bar got; skipping ahead counts "
                       "as watched, and the bar never shows less than 10%.", style="grey42"))
    console.print(Text(f"  note: YouTube shows no length or progress for Shorts, so the {missing:,} Shorts count as "
                       "entries but not as watch time (the calendar assumes ~30 s each); "
                       f"the same goes for {streams:,} streams over 12 h. "
                       "History only gives the day, not the time of day.", style="grey42"))
