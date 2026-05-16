"""
Phase 1 tests — core/youtube.py
Run: venv/bin/python -m pytest tests/test_youtube.py -v
"""
import pytest
from core.youtube import (
    _parse_channel_url,
    _parse_duration_iso,
    get_channel_videos,
    search_videos,
    filter_by_duration,
    sort_by_views,
    sort_by_date,
    exclude_shorts,
    VideoMeta,
)


# ─── unit tests (no network) ────────────────────────────────────

def _make_video(vid_id="abc12345678", title="Test", duration=300, view_count=1000, upload_date="20240101"):
    return VideoMeta(vid_id, title, duration, view_count, upload_date, "Channel", f"https://youtube.com/watch?v={vid_id}")


def test_parse_channel_url_handle():
    assert _parse_channel_url("@lexfridman") == "https://youtube.com/@lexfridman"


def test_parse_channel_url_bare():
    assert _parse_channel_url("lexfridman") == "https://youtube.com/@lexfridman"


def test_parse_channel_url_full():
    url = "https://youtube.com/channel/UCxxxx"
    assert _parse_channel_url(url) == url


def test_parse_duration_iso_full():
    assert _parse_duration_iso("PT3H14M15S") == 11655


def test_parse_duration_iso_minutes_only():
    assert _parse_duration_iso("PT5M30S") == 330


def test_parse_duration_iso_invalid():
    assert _parse_duration_iso("") == 0
    assert _parse_duration_iso(None) == 0


def test_exclude_shorts():
    videos = [_make_video(duration=45), _make_video(duration=61), _make_video(duration=600)]
    result = exclude_shorts(videos)
    assert len(result) == 2
    assert all(v.duration > 60 for v in result)


def test_filter_by_duration():
    videos = [_make_video(duration=d) for d in [100, 300, 600, 1200]]
    assert len(filter_by_duration(videos, min_sec=300)) == 3
    assert len(filter_by_duration(videos, max_sec=600)) == 3
    assert len(filter_by_duration(videos, min_sec=300, max_sec=600)) == 2


def test_sort_by_views():
    videos = [_make_video(view_count=v) for v in [100, 5000, 1000]]
    result = sort_by_views(videos)
    assert result[0].view_count == 5000


def test_sort_by_date():
    videos = [_make_video(upload_date=d) for d in ["20230101", "20240601", "20220101"]]
    result = sort_by_date(videos)
    assert result[0].upload_date == "20240601"


# ─── integration tests (real network, require .env) ─────────────

@pytest.mark.integration
def test_get_channel_videos_lexfridman():
    videos = get_channel_videos("@lexfridman", limit=5)
    assert len(videos) == 5
    for v in videos:
        assert len(v.video_id) == 11
        assert v.duration > 60
        assert v.title


@pytest.mark.integration
def test_get_channel_videos_excludes_shorts():
    videos = get_channel_videos("@lexfridman", limit=10, exclude_shorts_flag=True)
    assert all(v.duration > 60 for v in videos)


@pytest.mark.integration
def test_get_channel_videos_nonexistent():
    videos = get_channel_videos("@this_channel_does_not_exist_xyz_abc_123")
    assert videos == []


@pytest.mark.integration
def test_search_videos_machine_learning():
    videos = search_videos("machine learning", order="viewCount", limit=20)
    assert len(videos) <= 20
    assert all(v.video_id for v in videos)
    assert all(v.duration > 60 for v in videos)
    ids = [v.video_id for v in videos]
    assert len(ids) == len(set(ids)), "Дублей не должно быть"
