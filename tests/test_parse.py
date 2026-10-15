import json
from datetime import date
from pathlib import Path

import pytest

from ythist.parse import parse_page

TODAY = date(2026, 10, 15)


def lockup(vid, title, channel, views_text, duration, percent=None, handle=None, cid=None):
    overlay = {"badges": [{"thumbnailBadgeViewModel": {"text": duration}}]}
    if percent is not None:
        overlay["progressBar"] = {"thumbnailOverlayProgressBarViewModel": {"startPercent": percent}}
    image = {}
    if cid:
        image = {"decoratedAvatarViewModel": {"avatar": {}, "rendererContext": {"commandContext": {"onTap": {
            "innertubeCommand": {"browseEndpoint": {"browseId": cid, "canonicalBaseUrl": f"/{handle}"}}}}}}}
    return {"lockupViewModel": {
        "contentId": vid, "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
        "contentImage": {"thumbnailViewModel": {"overlays": [{"thumbnailBottomOverlayViewModel": overlay}]}},
        "metadata": {"lockupMetadataViewModel": {
            "title": {"content": title},
            "image": image,
            "metadata": {"contentMetadataViewModel": {"metadataRows": [{"metadataParts": [
                {"text": {"content": channel}},
                {"text": {"content": views_text}, "accessibilityLabel": "views",
                 "leadingIcon": {"name": "PLAY_ARROW_OUTLINED"}},
            ]}]}},
        }},
    }}


def short(vid, title, views):
    return {"shortsLockupViewModel": {
        "entityId": f"history-shorts-shelf-item-{vid}",
        "overlayMetadata": {"primaryText": {"content": title}, "secondaryText": {"content": views}},
        "onTap": {"innertubeCommand": {"reelWatchEndpoint": {"videoId": vid}}},
    }}


def section(label, *items):
    return {"itemSectionRenderer": {
        "header": {"itemSectionHeaderRenderer": {"title": {"simpleText": label}}},
        "contents": list(items)}}


INITIAL = {"contents": {"twoColumnBrowseResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
    "sectionListRenderer": {"contents": [
        section("Today",
                lockup("aaaaaaaaaaa", "First", "Chan A", "1M", "18:31", 10, "@chana", "UCa")),
        section("Monday",
                lockup("bbbbbbbbbbb", "Second", "Chan B", "1.3K", "1:00:00", 100),
                {"reelShelfRenderer": {"title": {"runs": [{"text": "Shorts"}]},
                                       "items": [short("ccccccccccc", "A short", "5.8M views")]}}),
        {"continuationItemRenderer": {"continuationEndpoint": {}}},
    ]}}}}]}}}

CONT = {"onResponseReceivedActions": [{"appendContinuationItemsAction": {"continuationItems": [
    section("Sep 3, 2024", lockup("ddddddddddd", "Old", "Chan C", "No views", "LIVE")),
]}}]}


def test_initial_page():
    entries, more = parse_page(INITIAL, TODAY)
    assert more is True
    assert [e.video_id for e in entries] == ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"]
    a, b, c = entries
    assert (a.title, a.channel_name, a.views, a.duration_s, a.percent) == ("First", "Chan A", 1_000_000, 1111, 10)
    assert (a.channel_id, a.channel_handle) == ("UCa", "@chana")
    assert a.watch_date == "2026-10-15" and a.date_label == "Today"
    assert a.watched_s == 111
    assert b.watch_date == "2026-10-12" and b.views == 1300 and b.watched_s == 3600
    assert c.is_short and c.title == "A short" and c.views == 5_800_000 and c.channel_name is None


def test_continuation_page():
    entries, more = parse_page(CONT, TODAY)
    assert more is False
    (d,) = entries
    assert d.watch_date == "2024-09-03" and d.views == 0 and d.duration_s is None and d.percent is None


def test_carries_date_into_headerless_section():
    page = {"onResponseReceivedActions": [{"appendContinuationItemsAction": {"continuationItems": [
        {"itemSectionRenderer": {"contents": [lockup("eeeeeeeeeee", "x", "y", "1K", "1:00")]}}]}}]}
    entries, _ = parse_page(page, TODAY, last_label="Yesterday")
    assert entries[0].watch_date == "2026-10-14"


RAW = Path.home() / ".local/share/ythist/raw/probe"


@pytest.mark.skipif(not RAW.exists(), reason="no captured pages on this machine")
def test_real_captures_parse_cleanly():
    for f in sorted(RAW.glob("*.json")):
        data = json.loads(f.read_text())
        entries, _ = parse_page(data, TODAY)
        assert entries, f
        videos = [e for e in entries if not e.is_short]
        # Every regular video should have the core fields.
        assert all(e.title and e.channel_name and e.duration_s for e in videos if e.duration_s is not None)
        assert sum(e.views is not None for e in entries) / len(entries) > 0.95
        assert sum(e.channel_id is not None for e in videos) / len(videos) > 0.9
        assert sum(e.percent is not None for e in videos) / len(videos) > 0.9
        assert all(e.watch_date for e in entries)
