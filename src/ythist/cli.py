"""ythist — scrape, store and analyse your YouTube watch history."""

from __future__ import annotations

import argparse
import csv
import json
import signal
import sys
from pathlib import Path

from rich.console import Console

from . import db
from .paths import DB_PATH


def cmd_scrape(a: argparse.Namespace, console: Console) -> None:
    from .enrich import enrich
    from .scrape import scrape

    scrape(console, tick_ms=a.delay, images=a.images, heap_limit_mb=a.heap_limit, keep_open=a.keep_open, max_pages=a.max_pages)
    if not a.no_enrich:
        enrich(db.connect(), console)
    if not a.no_stats:
        from .stats import report
        report(db.connect(), console)


def cmd_enrich(a: argparse.Namespace, console: Console) -> None:
    from .enrich import enrich
    enrich(db.connect(), console)


def cmd_stats(a: argparse.Namespace, console: Console) -> None:
    from .stats import report
    report(db.connect(), console, since=a.since, top=a.top)


def cmd_export(a: argparse.Namespace, console: Console) -> None:
    con = db.connect()
    rows = [dict(r) for r in con.execute("SELECT * FROM history ORDER BY watch_date DESC, position")]
    out = Path(a.output or f"yt-history.{a.format}")
    if a.format == "json":
        out.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        with out.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["video_id"])
            w.writeheader()
            w.writerows(rows)
    console.print(f"[green3]✓[/] {len(rows):,} rows → {out}")


def _sigterm(*_: object) -> None:
    raise KeyboardInterrupt  # same clean path as Ctrl-C: run marked, browser closed


def main(argv: list[str] | None = None) -> None:
    signal.signal(signal.SIGTERM, _sigterm)
    p = argparse.ArgumentParser(prog="ythist", description=__doc__)
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("scrape", help="scroll your history in Brave and store everything (default)")
    s.add_argument("--delay", type=int, default=180, help="ms between PageDown presses (default 180)")
    s.add_argument("--max-pages", type=int, help="stop after N feed pages (≈200 entries each)")
    s.add_argument("--images", action="store_true", help="load thumbnails (prettier, much more RAM)")
    s.add_argument("--heap-limit", type=int, default=1200, metavar="MB",
                   help="reload the page and resume when the tab's JS heap passes this (default 1200)")
    s.add_argument("--keep-open", action="store_true", help="leave the browser running afterwards")
    s.add_argument("--no-enrich", action="store_true", help="skip filling Shorts channels via oEmbed")
    s.add_argument("--no-stats", action="store_true", help="don't print the report afterwards")
    s.set_defaults(fn=cmd_scrape)

    e = sub.add_parser("enrich", help="fill missing channels (Shorts) via oEmbed")
    e.set_defaults(fn=cmd_enrich)

    st = sub.add_parser("stats", help="print the statistics report")
    st.add_argument("--since", help="only entries on/after this date (YYYY-MM-DD)")
    st.add_argument("--top", type=int, default=15, help="rows in top lists (default 15)")
    st.set_defaults(fn=cmd_stats)

    x = sub.add_parser("export", help="dump the history as CSV or JSON")
    x.add_argument("-f", "--format", choices=["csv", "json"], default="csv")
    x.add_argument("-o", "--output", help="output file (default yt-history.<format>)")
    x.set_defaults(fn=cmd_export)

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in sub.choices and argv[0] not in ("-h", "--help"):
        argv.insert(0, "scrape")
    a = p.parse_args(argv)
    console = Console()
    console.print(f"[grey50]database: {DB_PATH}")
    try:
        a.fn(a, console)
    except KeyboardInterrupt:
        console.print("[yellow3]interrupted — everything scraped so far is saved")
        sys.exit(130)
