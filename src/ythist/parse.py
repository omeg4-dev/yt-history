"""Turn YouTube history JSON (ytInitialData or a browse continuation) into Entries."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any

from .model import Entry, iso, parse_duration, parse_views, resolve_date


def find(obj: Any, key: str) -> Iterator[Any]:
    """Yield every value stored under `key`, depth-first, in document order."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            else:
                yield from find(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from find(v, key)


def first(obj: Any, key: str, default: Any = None) -> Any:
    return next(find(obj, key), default)


def _text(t: Any) -> str | None:
    if not isinstance(t, dict):
        return None
    if "simpleText" in t:
        return t["simpleText"]
    if "runs" in t:
        return "".join(r.get("text", "") for r in t["runs"])
    return t.get("content")


def _top_items(data: dict) -> list:
    """The ordered list of sections, for either page shape."""
    for action in data.get("onResponseReceivedActions", []):
        if items := action.get("appendContinuationItemsAction", {}).get("continuationItems"):
            return items
    return first(data.get("contents", {}), "sectionListRenderer", {}).get("contents", [])


def _video(lv: dict, label: str, day: str) -> Entry:
    meta = lv.get("metadata", {}).get("lockupMetadataViewModel", {})
    parts = [p for row in first(meta, "metadataRows", []) for p in row.get("metadataParts", [])]
    views_part = next((p for p in parts
                       if "PLAY_ARROW" in p.get("leadingIcon", {}).get("name", "")
                       or "view" in p.get("accessibilityLabel", "").lower()), None)
    channel_part = next((p for p in parts if p is not views_part), None)
    browse = first(meta.get("image", {}), "browseEndpoint") or first(parts, "browseEndpoint") or {}
    handle = (browse.get("canonicalBaseUrl") or "").lstrip("/") or None

    image = lv.get("contentImage", {})
    duration = next((d for t in find(image, "thumbnailBadgeViewModel")
                     if (d := parse_duration(t.get("text"))) is not None), None)
    progress = first(image, "thumbnailOverlayProgressBarViewModel")

    return Entry(
        video_id=lv["contentId"],
        title=_text(meta.get("title")) or "",
        watch_date=day,
        date_label=label,
        channel_name=_text(channel_part.get("text")) if channel_part else None,
        channel_id=browse.get("browseId"),
        channel_handle=handle if handle and handle.startswith("@") else None,
        duration_s=duration,
        views=parse_views(_text(views_part.get("text"))) if views_part else None,
        percent=float(progress["startPercent"]) if progress and "startPercent" in progress else None,
    )


def _short(sv: dict, label: str, day: str) -> Entry | None:
    vid = first(sv.get("onTap", {}), "videoId") or sv.get("entityId", "").rsplit("-", 1)[-1]
    if not vid:
        return None
    om = sv.get("overlayMetadata", {})
    return Entry(
        video_id=vid,
        title=_text(om.get("primaryText")) or "",
        watch_date=day,
        date_label=label,
        views=parse_views(_text(om.get("secondaryText"))),
        is_short=True,
    )


def parse_page(data: dict, today: date | None = None,
               last_label: str | None = None) -> tuple[list[Entry], bool]:
    """Returns (entries in page order, whether a continuation follows).

    `last_label` is the header of the previous page's last section, used when
    a page starts mid-day without its own header.
    """
    today = today or date.today()
    entries: list[Entry] = []
    more = False
    label = last_label or "Today"
    day = iso(resolve_date(label, today) or today)

    for item in _top_items(data):
        if "continuationItemRenderer" in item:
            more = True
            continue
        sec = item.get("itemSectionRenderer")
        if not sec:
            continue
        if header := _text(first(sec.get("header", {}), "title")):
            label = header
            if resolved := resolve_date(header, today):
                day = iso(resolved)
        for content in sec.get("contents", []):
            for lv in find(content, "lockupViewModel"):
                if lv.get("contentId") and lv.get("contentType", "LOCKUP_CONTENT_TYPE_VIDEO") == "LOCKUP_CONTENT_TYPE_VIDEO":
                    entries.append(_video(lv, label, day))
            for sv in find(content, "shortsLockupViewModel"):
                if e := _short(sv, label, day):
                    entries.append(e)
    return entries, more
