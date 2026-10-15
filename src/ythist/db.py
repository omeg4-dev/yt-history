"""SQLite storage. One row per video, one row per (video, day) history entry."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from .model import Entry
from .paths import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    video_id       TEXT PRIMARY KEY,
    title          TEXT,
    channel_name   TEXT,
    channel_id     TEXT,
    channel_handle TEXT,
    duration_s     INTEGER,
    views          INTEGER,
    is_short       INTEGER NOT NULL DEFAULT 0,
    oembed_tried   INTEGER NOT NULL DEFAULT 0,
    first_seen     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entries (
    video_id     TEXT NOT NULL REFERENCES videos(video_id),
    watch_date   TEXT NOT NULL,          -- ISO date resolved from the section header
    date_label   TEXT,                   -- header as YouTube showed it ("Today", "Sep 3")
    position     INTEGER,                -- 0 = most recent, in the latest run that saw it
    percent      REAL,                   -- red progress bar, 0-100 (NULL = no bar shown)
    watched_s    INTEGER,                -- percent * duration
    run_id       INTEGER REFERENCES runs(id),
    PRIMARY KEY (video_id, watch_date)
);
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    seen        INTEGER DEFAULT 0,
    new         INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'running'
);
CREATE INDEX IF NOT EXISTS entries_date ON entries(watch_date);
DROP VIEW IF EXISTS history;
CREATE VIEW history AS
    SELECT e.watch_date, e.date_label, e.position, e.percent,
           CASE WHEN v.duration_s > 43200 THEN NULL ELSE e.watched_s END AS watched_s,
           v.video_id, v.title, v.channel_name, v.channel_handle, v.channel_id,
           v.duration_s, v.views, v.is_short,
           'https://www.youtube.com/watch?v=' || v.video_id AS url
    FROM entries e JOIN videos v USING (video_id);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def start_run(con: sqlite3.Connection) -> int:
    cur = con.execute("INSERT INTO runs (started_at) VALUES (?)", (now(),))
    con.commit()
    return cur.lastrowid


def finish_run(con: sqlite3.Connection, run_id: int, seen: int, new: int, status: str) -> None:
    con.execute(
        "UPDATE runs SET finished_at=?, seen=?, new=?, status=? WHERE id=?",
        (now(), seen, new, status, run_id),
    )
    con.commit()


def upsert(con: sqlite3.Connection, run_id: int, entries: list[Entry]) -> int:
    """Store a batch; returns how many (video, day) entries were new."""
    ts = now()
    new = 0
    for e in entries:
        con.execute(
            """INSERT INTO videos (video_id, title, channel_name, channel_id, channel_handle,
                                   duration_s, views, is_short, first_seen, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(video_id) DO UPDATE SET
                 title=excluded.title,
                 channel_name=COALESCE(excluded.channel_name, channel_name),
                 channel_id=COALESCE(excluded.channel_id, channel_id),
                 channel_handle=COALESCE(excluded.channel_handle, channel_handle),
                 duration_s=COALESCE(excluded.duration_s, duration_s),
                 views=COALESCE(excluded.views, views),
                 is_short=excluded.is_short,
                 updated_at=excluded.updated_at""",
            (e.video_id, e.title, e.channel_name, e.channel_id, e.channel_handle,
             e.duration_s, e.views, int(e.is_short), ts, ts),
        )
        existed = con.execute(
            "SELECT 1 FROM entries WHERE video_id=? AND watch_date=?", (e.video_id, e.watch_date)
        ).fetchone()
        new += existed is None
        con.execute(
            """INSERT INTO entries (video_id, watch_date, date_label, position, percent, watched_s, run_id)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(video_id, watch_date) DO UPDATE SET
                 date_label=excluded.date_label, position=excluded.position,
                 percent=COALESCE(excluded.percent, percent),
                 watched_s=COALESCE(excluded.watched_s, watched_s), run_id=excluded.run_id""",
            (e.video_id, e.watch_date, e.date_label, e.position, e.percent, e.watched_s, run_id),
        )
    con.commit()
    return new
