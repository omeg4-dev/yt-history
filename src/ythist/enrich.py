"""Fill in channels the history feed leaves out (Shorts) via YouTube's public oEmbed."""

from __future__ import annotations

import json
import sqlite3
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn

OEMBED = "https://www.youtube.com/oembed?format=json&url="


def _fetch(video_id: str) -> tuple[str, dict | None]:
    url = OEMBED + urllib.parse.quote(f"https://www.youtube.com/watch?v={video_id}")
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return video_id, json.load(r)
    except (OSError, ValueError):
        return video_id, None  # private/removed videos 401/404


def enrich(con: sqlite3.Connection, console: Console, workers: int = 12) -> int:
    ids = [r[0] for r in con.execute(
        "SELECT video_id FROM videos WHERE channel_name IS NULL AND oembed_tried = 0")]
    if not ids:
        return 0
    done = 0
    with Progress(SpinnerColumn(style="#ff3d3d"), TextColumn("[bold]filling in channels for Shorts"),
                  BarColumn(complete_style="#ff3d3d"), MofNCompleteColumn(), TimeRemainingColumn(),
                  console=console, transient=True) as prog:
        task = prog.add_task("", total=len(ids))
        with ThreadPoolExecutor(workers) as pool:
            for fut in as_completed(pool.submit(_fetch, v) for v in ids):
                vid, data = fut.result()
                if data:
                    handle = data.get("author_url", "").rsplit("/", 1)[-1]
                    con.execute(
                        "UPDATE videos SET channel_name=?, channel_handle=COALESCE(channel_handle, ?), oembed_tried=1 WHERE video_id=?",
                        (data.get("author_name"), handle if handle.startswith("@") else None, vid))
                    done += 1
                else:
                    con.execute("UPDATE videos SET oembed_tried=1 WHERE video_id=?", (vid,))
                prog.advance(task)
    con.commit()
    console.print(f"[green3]✓[/] channels filled for {done:,}/{len(ids):,} videos "
                  f"[grey50]({len(ids) - done} private/removed)")
    return done
