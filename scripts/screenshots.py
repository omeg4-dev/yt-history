"""Render README screenshots from a generated demo history (never your real one).

    uv run python scripts/screenshots.py

Builds a fake database in a temp dir, renders the live dashboard and the stats
report with rich, and turns the SVGs into PNGs with a headless Chromium-family
browser. Output: docs/dashboard.png, docs/stats.png.
"""

from __future__ import annotations

import math
import os
import random
import re
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp(prefix="ythist-demo-")  # before importing ythist

from playwright.sync_api import sync_playwright  # noqa: E402
from rich.console import Console  # noqa: E402

from ythist import browser, db  # noqa: E402
from ythist.model import Entry  # noqa: E402
from ythist.scrape import State, render  # noqa: E402
from ythist.stats import report  # noqa: E402

random.seed(7)
TODAY = date(2026, 10, 15)

CHANNELS = [  # (name, typical length in minutes, how much you finish 0-1)
    ("Curious Circuits", 22, 0.85), ("Quiet Orbit", 35, 0.7), ("Pixel Garage", 14, 0.9),
    ("Kitchen Physics", 11, 0.8), ("Field Notes", 28, 0.6), ("Rail & Rust", 41, 0.55),
    ("Lofi Lantern", 90, 0.4), ("Datenküche", 18, 0.75), ("Slow Mo Lab", 9, 0.95),
    ("Map Men Club", 16, 0.85), ("Terminal Tuesday", 25, 0.65), ("Deep Sea Desk", 48, 0.5),
    ("Small Engine Diaries", 31, 0.6), ("Synth Basement", 12, 0.7), ("History in Ten", 10, 0.9),
    ("The Patient Gardener", 20, 0.45), ("Retro Ports", 37, 0.6), ("Late Night Linux", 55, 0.5),
]
TOPICS = ["the fastest way to", "why nobody talks about", "I rebuilt", "a quiet tour of", "what happens when",
          "the hidden cost of", "fixing", "a week with", "ranking every", "the strange history of",
          "explaining", "testing"]
THINGS = ["mechanical keyboards", "old trains", "tiling window managers", "deep-sea fish", "sourdough",
          "analog synths", "Roman roads", "tiny engines", "dead game consoles", "city maps",
          "bike repairs", "lighthouses", "the metric system", "solar ovens", "pinball machines",
          "pixel art", "cheap microphones", "glacier caves"]
SHORTS = ["wait for it…", "this tool is genius", "satisfying cleanup", "one-minute recipe", "cat vs laser",
          "tiny desk setup", "how is this legal", "perfect loop", "rate this build", "unexpected ending"]


def title() -> str:
    t = f"{random.choice(TOPICS)} {random.choice(THINGS)}"
    return t[0].upper() + t[1:]


def video(day: date, label: str) -> Entry:
    weights = [1 / (i + 1) ** 0.9 for i in range(len(CHANNELS))]
    name, minutes, finish = random.choices(CHANNELS, weights)[0]
    dur = max(45, int(random.lognormvariate(math.log(minutes * 60), 0.5)))
    if random.random() < finish:
        pct = 100.0 if random.random() < 0.8 else float(random.randint(90, 99))
    else:
        pct = float(random.choice([10] * 3 + list(range(11, 90))))
    return Entry(
        video_id="".join(random.choices("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_", k=11)),
        title=title(), watch_date=day.isoformat(), date_label=label, channel_name=name,
        channel_handle="@" + name.lower().replace(" ", "").replace("&", "and"),
        duration_s=dur, views=int(random.lognormvariate(math.log(400_000), 1.8)), percent=pct)


def short(day: date, label: str) -> Entry:
    e = video(day, label)
    e.title, e.is_short, e.duration_s, e.percent = random.choice(SHORTS) + " #shorts", True, None, None
    return e


def label_for(d: date) -> str:
    delta = (TODAY - d).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    if delta < 7:
        return d.strftime("%A")
    return f"{d:%b} {d.day}" + (f", {d.year}" if d.year != TODAY.year else "")


def history() -> list[Entry]:
    out: list[Entry] = []
    for back in range(0, 600):
        d = TODAY - timedelta(days=back)
        if random.random() < 0.08:
            continue  # days off
        weekend = d.weekday() >= 4
        n = max(0, int(random.gauss(9 if weekend else 5, 3)))
        lab = label_for(d)
        day = [video(d, lab) for _ in range(n)]
        if random.random() < 0.25:
            day += [short(d, lab) for _ in range(random.randint(3, 25))]
        random.shuffle(day)
        out += day
    for i, e in enumerate(out):
        e.position = i
    return out


def to_png(svg: Path, png: Path) -> None:
    proc = browser.launch("about:blank", port=9444)  # throwaway profile is fine for a local file
    try:
        with sync_playwright() as pw:
            b, page = browser.connect(pw, port=9444)
            # Load the SVG at its natural size (2x for sharpness) so nothing gets squeezed.
            cdp = page.context.new_cdp_session(page)
            cdp.send("Emulation.setDeviceMetricsOverride",
                     {"width": 1400, "height": 5000, "deviceScaleFactor": 2, "mobile": False})
            # rich's SVG only has a viewBox; pin it to its natural width inside a wrapper page.
            markup = svg.read_text()
            width = float(re.search(r'viewBox="0 0 ([\d.]+)', markup).group(1))
            html = svg.with_suffix(".html")
            html.write_text(f'<body style="margin:0;background:#0d0d0d"><div style="width:{width}px">{markup}</div>')
            page.goto(html.as_uri())
            page.wait_for_timeout(1500)  # web font
            page.locator("svg").first.screenshot(path=str(png))
            html.unlink()
            b.new_browser_cdp_session().send("Browser.close")
    finally:
        if proc and proc.poll() is None:
            proc.terminate()


def main() -> None:
    DOCS.mkdir(exist_ok=True)
    entries = history()
    con = db.connect()
    run = db.start_run(con)
    db.upsert(con, run, entries)
    db.finish_run(con, run, len(entries), len(entries), "done")

    # Dashboard frame: ~40 % through the scroll.
    s = State(phase="scrolling", pages=31, heap=412_000_000, heap_peak=688_000_000, nodes=11_840, recycles=0)
    s.started -= 96
    s.add(entries[: int(len(entries) * 0.4)])
    s.new = 214
    con_w = 150
    c = Console(width=con_w, height=38, record=True, force_terminal=True, color_system="truecolor")
    c.print(render(s, con_w), height=38)
    c.save_svg(str(DOCS / "dashboard.svg"), title="ythist scrape")

    c = Console(width=150, record=True, force_terminal=True, color_system="truecolor")
    report(con, c, top=10)
    c.save_svg(str(DOCS / "stats.svg"), title="ythist stats")

    for name in ("dashboard", "stats"):
        to_png(DOCS / f"{name}.svg", DOCS / f"{name}.png")
        (DOCS / f"{name}.svg").unlink()
        print("wrote", DOCS / f"{name}.png")


if __name__ == "__main__":
    sys.exit(main())
