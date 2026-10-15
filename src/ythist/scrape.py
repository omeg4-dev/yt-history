"""Scroll the history feed in a visible browser and capture YouTube's own JSON."""

from __future__ import annotations

import gzip
import hashlib
import json
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import date

from playwright.sync_api import Page, Response, Route, sync_playwright
from rich.align import Align
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import browser, db
from .fmt import bar, clock, dur, num, pct_color, spark
from .model import Entry
from .parse import first, parse_page
from .paths import RAW_DIR

HISTORY_URL = "https://www.youtube.com/feed/history?hl=en"
LOGIN_URL = ("https://accounts.google.com/ServiceLogin?service=youtube"
             "&continue=https%3A%2F%2Fwww.youtube.com%2Ffeed%2Fhistory")
ACCENT = "#ff3d3d"

# Long histories: YouTube keeps every loaded day both in the DOM and in its data model
# (ytInitialData grows ~15 KB per entry). Drop old rendered sections and empty the old
# sections' contents in place — same array length, so YouTube's own indices stay valid.
PRUNE_JS = """(keep) => {
  const secs = document.querySelectorAll('ytd-section-list-renderer #contents > ytd-item-section-renderer');
  for (let i = 0; i < secs.length - keep; i++) secs[i].remove();
  const sl = document.querySelector('ytd-section-list-renderer');
  const items = sl && sl.data && sl.data.contents;
  if (items) for (let i = 0; i < items.length - keep - 1; i++) {
    const sec = items[i] && items[i].itemSectionRenderer;
    if (sec && sec.contents && sec.contents.length) sec.contents = [];
  }
}"""
HEALTH_JS = """() => ({heap: performance.memory ? performance.memory.usedJSHeapSize : 0,
                     nodes: document.getElementsByTagName('*').length})"""
# Thumbnails, avatars, hover previews and web fonts: nothing we read, lots of RAM.
BLOCKED_URLS = ["*://i.ytimg.com/*", "*://i9.ytimg.com/*", "*://*.ggpht.com/*",
                "*://yt3.googleusercontent.com/*", "*://*.googlevideo.com/*", "*://fonts.gstatic.com/*"]
REMAINING_JS = """() => {
  const el = document.scrollingElement;
  return el.scrollHeight - (el.scrollTop + window.innerHeight);
}"""
# PageDown only scrolls the document when nothing (e.g. the search box) holds focus.
UNFOCUS_JS = "() => document.activeElement && document.activeElement.blur()"
SPINNER_JS = """() => !!document.querySelector('ytd-continuation-item-renderer')"""


@dataclass
class State:
    phase: str = "starting"
    note: str = ""
    started: float = field(default_factory=time.monotonic)
    pages: int = 0
    videos: int = 0
    shorts: int = 0
    new: int = 0
    watched_s: int = 0
    heap: int = 0
    heap_peak: int = 0
    recycles: int = 0
    nodes: int = 0
    runtime_s: int = 0
    label: str = "–"
    day: str | None = None
    first_day: str | None = None
    recent: deque = field(default_factory=lambda: deque(maxlen=60))
    channels: Counter = field(default_factory=Counter)
    channel_time: Counter = field(default_factory=Counter)
    per_day: Counter = field(default_factory=Counter)
    pct_hist: list = field(default_factory=lambda: [0] * 10)

    def add(self, entries: list[Entry]) -> None:
        for e in entries:
            self.shorts += e.is_short
            self.videos += not e.is_short
            self.per_day[e.watch_date] += e.watched_s or (30 if e.is_short else 0)
            if e.channel_name:
                self.channels[e.channel_name] += 1
                self.channel_time[e.channel_name] += e.watched_s or 0
            self.watched_s += e.watched_s or 0
            self.runtime_s += e.duration_s or 0
            if e.percent is not None and not e.is_short:
                self.pct_hist[min(9, int(e.percent // 10))] += 1
            self.recent.appendleft(e)
            self.label, self.day = e.date_label, e.watch_date
            self.first_day = self.first_day or e.watch_date


def _elapsed(s: State) -> float:
    return time.monotonic() - s.started


def render(s: State, width: int = 160) -> Layout:
    total = s.videos + s.shorts
    rate = total / max(_elapsed(s), 1e-6)
    days_back = (date.today() - date.fromisoformat(s.day)).days if s.day else 0

    # --- header
    phase_style = {"done": "bold green3", "stalled": "bold yellow3", "error": "bold red3"}.get(s.phase, f"bold {ACCENT}")
    head = Text.assemble(
        ("  ▶ ", f"bold {ACCENT}"), ("ythist", "bold white"), ("  ·  youtube.com/feed/history", "grey58"),
        ("   ●  ", phase_style), (s.phase, phase_style), (f"  {s.note}" if s.note else "", "grey62"),
    )
    header = Panel(head, border_style=ACCENT, padding=(0, 1))

    # --- stat tiles
    tiles = Table.grid(padding=(0, 2))
    tiles.add_column(justify="right", style="grey62")
    tiles.add_column(style="bold white")

    def row(k: str, v: str, style: str = "bold white") -> None:
        tiles.add_row(k, Text(v, style=style))

    row("entries", f"{total:,}", f"bold {ACCENT}")
    row("videos", f"{s.videos:,}")
    row("shorts", f"{s.shorts:,}")
    row("new this run", f"{s.new:,}", "bold green3")
    row("pages", f"{s.pages}")
    row("rate", f"{rate:,.1f} /s")
    row("elapsed", dur(_elapsed(s)))
    if s.heap:
        row("browser heap", f"{s.heap / 1e6:,.0f} MB ↑{s.heap_peak / 1e6:,.0f}", "grey70")
        row("dom nodes", f"{s.nodes:,}", "grey70")
        row("recycles", f"{s.recycles}", "grey70")
    tiles.add_row("", "")
    row("scrolling past", s.label, "bold yellow3")
    row("date", s.day or "–")
    row("depth", f"{days_back:,} days back")
    tiles.add_row("", "")
    row("watched", dur(s.watched_s), "bold green3")
    row("of runtime", dur(s.runtime_s))
    eff = s.watched_s / s.runtime_s if s.runtime_s else 0
    row("completion", f"{eff:.0%}")
    hist = Text("  " + spark(s.pct_hist), style="green3")
    left = Panel(Group(tiles, Text(""), Text(" % watched  0 → 100", style="grey50"), hist),
                 title="[bold]live", border_style="grey35")

    # --- ticker
    feed = Table(box=None, expand=True, show_header=True, header_style="grey50", padding=(0, 1))
    feed.add_column("day", width=5, style="grey62", no_wrap=True)
    feed.add_column("channel", ratio=2, min_width=12, no_wrap=True, style="cyan", overflow="ellipsis")
    feed.add_column("title", ratio=5, min_width=16, no_wrap=True, overflow="ellipsis")
    feed.add_column("watched", width=14, no_wrap=True)
    feed.add_column("len", width=8, justify="right", style="grey62", no_wrap=True)
    feed.add_column("views", width=6, justify="right", style="grey62", no_wrap=True)
    for i, e in enumerate(s.recent):
        fade = "" if i < 4 else "dim"
        if e.is_short:
            w = Text("▮ short", style="magenta")
        elif e.percent is None:
            w = Text("·", style="grey42")
        else:
            w = Text(bar(e.percent / 100, 8), style=pct_color(e.percent)) + Text(f" {e.percent:>3.0f}%", style="grey62")
        feed.add_row(e.watch_date[5:], e.channel_name or ("—" if e.is_short else "?"),
                     Text(e.title, style=fade or "white"), w, clock(e.duration_s), num(e.views),
                     style=fade)
    middle = Panel(feed, title="[bold]scrolling past", border_style="grey35")

    # --- top channels
    top = Table.grid(padding=(0, 1), expand=True)
    top.add_column(no_wrap=True, ratio=1, min_width=14, style="cyan", overflow="ellipsis")
    top.add_column(no_wrap=True, width=10)
    top.add_column(justify="right", width=7, style="grey62", no_wrap=True)
    most = s.channel_time.most_common(30)
    hi = most[0][1] if most else 1
    for name, secs in most:
        top.add_row(name, Text(bar(secs / hi if hi else 0, 10), style=ACCENT), dur(secs, short=True))
    right = Panel(top, title="[bold]top channels · time", border_style="grey35")

    # --- activity strip: watch time per day, oldest left, newest right; squeezed to fit
    days = sorted(s.per_day)
    vals = [s.per_day[d] for d in days]
    room = max(10, width - 6)
    if len(vals) > room:
        k = -(-len(vals) // room)
        vals = [sum(vals[i:i + k]) for i in range(0, len(vals), k)]
    strip = spark(vals)
    foot = Panel(Text(strip or "waiting for data…", style=ACCENT, overflow="crop"),
                 title=f"[bold]watch time per day[/] [grey50]({len(days)} days: {days[0] if days else '–'} → {days[-1] if days else '–'})",
                 border_style="grey35")

    layout = Layout()
    layout.split_column(Layout(header, size=3), Layout(name="body"), Layout(foot, size=3))
    cols = [Layout(left, size=32), Layout(middle, ratio=3)]
    if width >= 120:  # channel leaderboard only when there is room for the ticker too
        cols.append(Layout(right, size=min(46, int(width * 0.26))))
    layout["body"].split_row(*cols)
    return layout


def _log(run_id: int, msg: str) -> None:
    d = RAW_DIR / f"run-{run_id:04d}"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "scrape.log", "a") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


def _save_raw(run_id: int, n: int, data: dict) -> None:
    d = RAW_DIR / f"run-{run_id:04d}"
    d.mkdir(parents=True, exist_ok=True)
    with gzip.open(d / f"page-{n:05d}.json.gz", "wt") as f:
        json.dump(data, f)


def _wait_for_login(page: Page, s: State, live: Live) -> None:
    if browser.logged_in(page):
        return
    s.phase, s.note = "waiting", "log into YouTube in the browser window (one time only)"
    page.goto(LOGIN_URL)
    while True:
        live.update(render(s, live.console.width))
        page.wait_for_timeout(1500)
        try:
            if "youtube.com" in page.url and browser.logged_in(page):
                return
        except Exception:  # mid-navigation
            pass


def scrape(console: Console, *, tick_ms: int = 180, keep_open: bool = False, images: bool = False,
           max_pages: int | None = None, stall_s: float = 45, heap_limit_mb: int = 1200) -> int:
    con = db.connect()
    run_id = db.start_run(con)
    s = State()
    proc = browser.launch("about:blank")
    pending: deque[dict] = deque()
    errors: list[str] = []
    seen_pages: set[str] = set()

    def on_response(r: Response) -> None:
        if "/youtubei/v1/browse" not in r.url or r.request.method != "POST":
            return
        try:
            data = r.json()
        except Exception as ex:  # body gone (navigation) or not JSON
            errors.append(str(ex)[:120])
            return
        # Other browse calls (guide, notifications…) share the endpoint; keep only feed continuations.
        if not any("appendContinuationItemsAction" in a for a in data.get("onResponseReceivedActions", [])):
            return
        # The same continuation response can be delivered more than once; each page's own
        # next-page token (or, on the last page, its body) identifies it.
        nxt = first(data, "continuationCommand")
        key = nxt.get("token") if nxt else hashlib.sha1(r.body()).hexdigest()
        if key in seen_pages:
            return
        seen_pages.add(key)
        pending.append(data)

    status = "error"
    position = 0
    last_label: str | None = None
    next_token: str | None = None
    resume: dict = {"token": None}  # set while a recycled page waits for its first continuation
    recycled_at = -1                # page count at the last recycle; one recycle per ingested page

    def ingest(data: dict) -> bool:
        nonlocal position, last_label, next_token
        if nxt := first(data, "continuationCommand"):
            next_token = nxt.get("token")
        entries, more = parse_page(data, last_label=last_label)
        for e in entries:
            e.position = position
            position += 1
        if entries:
            last_label = entries[-1].date_label
        s.pages += 1
        _log(run_id, f"page {s.pages}: {len(entries)} entries, more={more}")
        _save_raw(run_id, s.pages, data)
        s.new += db.upsert(con, run_id, entries)
        s.add(entries)
        return more

    def swap_token(route: Route) -> None:
        """On a recycled page, point YouTube's first "load more" at where we left off.

        The browser still builds and authenticates the request itself; only the
        continuation token in its (gzipped) JSON body is replaced.
        """
        token = resume["token"]
        body = route.request.post_data_buffer or b""
        zipped = body[:2] == b"\x1f\x8b"
        try:
            payload = json.loads(gzip.decompress(body) if zipped else body)
        except ValueError:
            return route.continue_()
        if not token or "continuation" not in payload:
            return route.continue_()
        resume["token"] = None
        _log(run_id, f"swap: resuming at token …{token[-16:]}")
        payload["continuation"] = token
        raw = json.dumps(payload).encode()
        route.continue_(post_data=gzip.compress(raw) if zipped else raw)

    def recycle(page: Page) -> None:
        """Reload the feed for a fresh JS heap, then resume from the saved token."""
        s.recycles += 1
        _log(run_id, f"recycle #{s.recycles} at page {s.pages}, heap {s.heap / 1e6:.0f} MB")
        s.note = f"recycling page (heap {s.heap / 1e6:,.0f} MB)"
        resume["token"] = next_token
        page.goto(HISTORY_URL, wait_until="domcontentloaded")
        page.wait_for_function("() => window.ytInitialData && window.ytInitialData.contents")
        s.heap = page.evaluate(HEALTH_JS)["heap"]
        _log(run_id, f"recycle done, heap {s.heap / 1e6:.0f} MB")

    with sync_playwright() as pw, Live(render(s), console=console, screen=True,
                                       refresh_per_second=12, transient=False) as live:
        b, page = browser.connect(pw)
        try:
            page.goto("https://www.youtube.com/?hl=en", wait_until="domcontentloaded")
            _wait_for_login(page, s, live)

            s.phase, s.note = "loading", "opening history"
            live.update(render(s, console.width))
            page.on("response", on_response)
            cdp = page.context.new_cdp_session(page)
            if not images:
                cdp.send("Network.enable")
                cdp.send("Network.setBlockedURLs", {"urls": BLOCKED_URLS})
            page.route("**/youtubei/v1/browse*", swap_token)
            page.goto(HISTORY_URL, wait_until="domcontentloaded")
            page.wait_for_function("() => window.ytInitialData && window.ytInitialData.contents")
            more = ingest(page.evaluate("() => window.ytInitialData"))
            if s.videos + s.shorts == 0:
                s.phase, s.note = "done", "history is empty (or paused)"
                status = "empty"
                more = False

            s.phase, s.note = "scrolling", ""
            last_data = time.monotonic()
            while more:
                while pending:
                    more = ingest(pending.popleft())
                    last_data = time.monotonic()
                    page.evaluate(PRUNE_JS, 4)
                    h = page.evaluate(HEALTH_JS)
                    s.heap, s.nodes = h["heap"], h["nodes"]
                    s.heap_peak = max(s.heap_peak, s.heap)
                if not more or (max_pages and s.pages >= max_pages):
                    break
                if (s.heap > heap_limit_mb * 1e6 and next_token and s.pages > recycled_at
                        and not resume["token"]):
                    # The raw number includes garbage; only recycle if it survives a full GC.
                    cdp.send("HeapProfiler.collectGarbage")
                    s.heap = page.evaluate(HEALTH_JS)["heap"]
                    if s.heap > heap_limit_mb * 1e6:
                        recycle(page)
                        recycled_at = s.pages
                        last_data = time.monotonic()
                page.evaluate(UNFOCUS_JS)
                page.keyboard.press("PageDown")
                remaining = page.evaluate(REMAINING_JS)
                if remaining < 50:
                    s.note = "loading more…"
                    # Nudge: YouTube only fires the continuation when the spinner enters the viewport.
                    if time.monotonic() - last_data > 6:
                        page.keyboard.press("PageUp")
                        page.wait_for_timeout(300)
                        page.keyboard.press("End")
                else:
                    s.note = ""
                if time.monotonic() - last_data > stall_s:
                    if not page.evaluate(SPINNER_JS):
                        more = False  # no spinner, no continuation: we're at the end
                        break
                    s.phase, s.note = "stalled", f"no new data for {stall_s:.0f}s — stopping"
                    status = "stalled"
                    break
                live.update(render(s, console.width))
                page.wait_for_timeout(tick_ms)

            if status != "stalled":
                status = "partial" if more else "done"
                s.phase = "done"
                s.note = "reached the end of your history" if not more else f"stopped after {s.pages} pages"
            live.update(render(s, console.width))
            page.wait_for_timeout(1200)
        except KeyboardInterrupt:
            status, s.phase, s.note = "interrupted", "stalled", "interrupted — data so far is saved"
            live.update(render(s, console.width))
        finally:
            db.finish_run(con, run_id, s.videos + s.shorts, s.new, status)
            _log(run_id, f"end: {status}, {s.pages} pages, {s.recycles} recycles, "
                         f"peak heap {s.heap_peak / 1e6:.0f} MB")
            if proc and not keep_open:
                try:  # brave-origin is a shell wrapper; ask the browser itself to quit
                    b.new_browser_cdp_session().send("Browser.close")
                except Exception:
                    proc.terminate()

    console.print(render_summary(s, status, errors))
    return run_id


def render_summary(s: State, status: str, errors: list[str]) -> Panel:
    t = Table.grid(padding=(0, 2))
    t.add_column(style="grey62", justify="right")
    t.add_column(style="bold")
    t.add_row("status", status)
    t.add_row("entries", f"{s.videos + s.shorts:,}  ({s.videos:,} videos, {s.shorts:,} shorts)")
    t.add_row("new", f"{s.new:,}")
    t.add_row("span", f"{s.day or '–'} → {s.first_day or '–'}")
    t.add_row("watched", f"{dur(s.watched_s)} of {dur(s.runtime_s)} runtime")
    t.add_row("pages", f"{s.pages} in {dur(_elapsed(s))}")
    if errors:
        t.add_row("skipped", f"{len(errors)} unreadable responses")
    return Panel(Align.left(t), title=f"[bold {ACCENT}]ythist scrape", border_style=ACCENT)
