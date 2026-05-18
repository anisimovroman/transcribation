import re
import subprocess
import logging
from dataclasses import dataclass, field
from typing import Optional
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import config as _cfg

logger = logging.getLogger(__name__)

_youtube_client = None
_quota_used = {"search_list": 0, "videos_list": 0}


@dataclass
class VideoMeta:
    video_id: str
    title: str
    duration: int
    view_count: int
    upload_date: str
    channel: str
    url: str
    has_captions: Optional[bool] = None


def get_youtube_client():
    global _youtube_client
    if _youtube_client is None:
        key = _cfg.YOUTUBE_API_KEY  # read dynamically — key may have been set after startup
        if not key:
            raise ValueError("YOUTUBE_API_KEY не задан в .env")
        try:
            _youtube_client = build("youtube", "v3", developerKey=key)
        except Exception as e:
            raise ValueError(f"Не удалось создать YouTube API клиент: {e}") from e
    return _youtube_client


_VIDEO_ID_RE = re.compile(r'[a-zA-Z0-9_-]{11}')
_VIDEO_URL_RE = re.compile(
    r'(?:v=|youtu\.be/|/shorts/|/embed/|/v/)([a-zA-Z0-9_-]{11})'
)


def parse_video_id(text: str) -> Optional[str]:
    """Extract YouTube video ID from a URL or raw ID string. Returns None if invalid."""
    text = text.strip()
    if not text:
        return None
    # Raw 11-char ID
    if re.fullmatch(r'[a-zA-Z0-9_-]{11}', text):
        return text
    # URL patterns: watch?v=, youtu.be/, /shorts/, /embed/, /v/
    m = _VIDEO_URL_RE.search(text)
    return m.group(1) if m else None


def get_videos_by_ids(video_ids: list[str]) -> list["VideoMeta"]:
    """Fetch VideoMeta for specific video IDs. Uses YouTube API if key set, else yt-dlp."""
    if not video_ids:
        return []
    import config as _cfg
    if _cfg.YOUTUBE_API_KEY:
        return _get_videos_api(video_ids)
    return _get_videos_ytdlp(video_ids)


def _get_videos_api(video_ids: list[str]) -> list["VideoMeta"]:
    yt = get_youtube_client()
    videos: list[VideoMeta] = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        try:
            resp = yt.videos().list(
                part="snippet,contentDetails,statistics",
                id=",".join(batch),
            ).execute()
        except HttpError as e:
            if e.resp.status == 403:
                raise QuotaExceededError("YouTube API квота исчерпана") from e
            raise
        _quota_used["videos_list"] += 1  # videos.list costs 1 unit per call regardless of batch size
        for item in resp.get("items", []):
            vid_id = item["id"]
            snippet = item["snippet"]
            duration = _parse_duration_iso(item["contentDetails"]["duration"])
            view_count = int(item["statistics"].get("viewCount", 0))
            upload_date = snippet.get("publishedAt", "")[:10].replace("-", "")
            videos.append(VideoMeta(
                video_id=vid_id,
                title=snippet["title"],
                duration=duration,
                view_count=view_count,
                upload_date=upload_date,
                channel=snippet["channelTitle"],
                url=f"https://youtube.com/watch?v={vid_id}",
            ))
    return videos


def _get_videos_ytdlp(video_ids: list[str]) -> list["VideoMeta"]:
    """Fetch metadata via yt-dlp when no API key is available."""
    urls = [f"https://youtube.com/watch?v={vid}" for vid in video_ids]
    cmd = [
        "yt-dlp", "--no-playlist", "--skip-download",
        "--print", "%(id)s\t%(title)s\t%(duration)s\t%(view_count)s\t%(upload_date)s\t%(channel)s",
        "--no-warnings", "--no-color",
        *urls,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        logger.error("yt-dlp timeout fetching video metadata")
        return []

    videos: list[VideoMeta] = []
    seen: set[str] = set()
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        vid_id, title, dur_str, views_str, date, channel = parts[:6]
        if vid_id in seen:
            continue
        seen.add(vid_id)
        try:
            duration = int(float(dur_str)) if dur_str not in ("", "NA") else 0
            view_count = int(float(views_str)) if views_str not in ("", "NA") else 0
        except (ValueError, TypeError):
            duration, view_count = 0, 0
        videos.append(VideoMeta(
            video_id=vid_id,
            title=title or vid_id,
            duration=duration,
            view_count=view_count,
            upload_date=date,
            channel=channel,
            url=f"https://youtube.com/watch?v={vid_id}",
        ))

    # For IDs not returned by yt-dlp, add stub entries so user can still transcribe
    returned_ids = {v.video_id for v in videos}
    for vid_id in video_ids:
        if vid_id not in returned_ids:
            logger.warning("yt-dlp returned no metadata for %s, using stub", vid_id)
            videos.append(VideoMeta(
                video_id=vid_id, title=vid_id, duration=0,
                view_count=0, upload_date="", channel="",
                url=f"https://youtube.com/watch?v={vid_id}",
            ))
    return videos


def _parse_channel_url(channel: str) -> str:
    channel = channel.strip()
    if channel.startswith("http"):
        return channel
    if channel.startswith("@"):
        return f"https://youtube.com/{channel}"
    if re.match(r"^UC[a-zA-Z0-9_-]{22}$", channel):
        return f"https://youtube.com/channel/{channel}"
    return f"https://youtube.com/@{channel}"


def _parse_duration_iso(iso_str: str) -> int:
    import re
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_str or "")
    if not m:
        return 0
    h, mn, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mn * 60 + s


def exclude_shorts(videos: list[VideoMeta]) -> list[VideoMeta]:
    return [v for v in videos if v.duration > 60]


def filter_by_duration(
    videos: list[VideoMeta],
    min_sec: Optional[int] = None,
    max_sec: Optional[int] = None,
) -> list[VideoMeta]:
    result = videos
    if min_sec is not None:
        result = [v for v in result if v.duration >= min_sec]
    if max_sec is not None:
        result = [v for v in result if v.duration <= max_sec]
    return result


def sort_by_views(videos: list[VideoMeta], descending: bool = True) -> list[VideoMeta]:
    return sorted(videos, key=lambda v: v.view_count, reverse=descending)


def sort_by_date(videos: list[VideoMeta], descending: bool = True) -> list[VideoMeta]:
    return sorted(videos, key=lambda v: v.upload_date, reverse=descending)


def get_channel_videos(
    channel_url: str,
    limit: Optional[int] = None,
    exclude_shorts_flag: bool = True,
    min_duration_sec: int = 0,
    max_duration_sec: Optional[int] = None,
    sort_by: str = "newest",  # newest | oldest | views | duration_asc | duration_desc
) -> list[VideoMeta]:
    url = _parse_channel_url(channel_url)
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--print", "%(id)s\t%(title)s\t%(duration)s\t%(view_count)s\t%(upload_date)s\t%(channel)s",
        "--no-warnings",
        "--no-color",
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        logger.error("yt-dlp timeout для %s", url)
        return []

    videos: list[VideoMeta] = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        vid_id, title, dur_str, views_str, date, channel = parts[:6]
        try:
            duration = int(float(dur_str)) if dur_str not in ("", "NA") else 0
            view_count = int(float(views_str)) if views_str not in ("", "NA") else 0
        except (ValueError, TypeError):
            continue
        videos.append(VideoMeta(
            video_id=vid_id,
            title=title,
            duration=duration,
            view_count=view_count,
            upload_date=date,
            channel=channel,
            url=f"https://youtube.com/watch?v={vid_id}",
        ))

    if exclude_shorts_flag:
        before = len(videos)
        videos = exclude_shorts(videos)
        logger.info("Filtered %d Shorts", before - len(videos))

    videos = filter_by_duration(videos, min_sec=min_duration_sec, max_sec=max_duration_sec)

    if sort_by == "oldest":
        videos = list(reversed(videos))
    elif sort_by == "views":
        videos = sort_by_views(videos)
    elif sort_by == "duration_asc":
        videos = sorted(videos, key=lambda v: v.duration)
    elif sort_by == "duration_desc":
        videos = sorted(videos, key=lambda v: v.duration, reverse=True)
    # "newest" = default playlist order (already newest-first on YouTube)

    if limit is not None:
        videos = videos[:limit]

    return videos


def search_videos(
    query: str,
    order: str = "relevance",
    duration_filter: str = "any",
    date_filter: Optional[str] = None,
    video_definition: str = "any",
    video_caption: str = "any",
    video_license: str = "any",
    event_type: str = "any",
    relevance_language: str = "",
    region_code: str = "",
    limit: int = 50,
) -> list[VideoMeta]:
    yt = get_youtube_client()
    videos: list[VideoMeta] = []
    page_token = None
    remaining = min(limit, 200)

    from datetime import datetime, timedelta
    published_after = None
    if date_filter == "hour":
        published_after = (datetime.utcnow() - timedelta(hours=1)).isoformat() + "Z"
    elif date_filter == "today":
        published_after = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat() + "Z"
    elif date_filter == "week":
        published_after = (datetime.utcnow() - timedelta(weeks=1)).isoformat() + "Z"
    elif date_filter == "month":
        published_after = (datetime.utcnow() - timedelta(days=30)).isoformat() + "Z"
    elif date_filter == "year":
        published_after = (datetime.utcnow() - timedelta(days=365)).isoformat() + "Z"

    while remaining > 0:
        page_limit = min(remaining, 50)
        params: dict = {
            "q": query,
            "part": "id,snippet",
            "type": "video",
            "maxResults": page_limit,
            "order": order,
        }
        if duration_filter and duration_filter != "any":
            params["videoDuration"] = duration_filter
        if video_definition and video_definition != "any":
            params["videoDefinition"] = video_definition
        if video_caption and video_caption != "any":
            params["videoCaption"] = video_caption
        if video_license and video_license != "any":
            params["videoLicense"] = video_license
        if event_type and event_type != "any":
            params["eventType"] = event_type
        if relevance_language:
            params["relevanceLanguage"] = relevance_language
        if region_code:
            params["regionCode"] = region_code
        if page_token:
            params["pageToken"] = page_token
        if published_after:
            params["publishedAfter"] = published_after

        try:
            response = yt.search().list(**params).execute()
        except HttpError as e:
            if e.resp.status == 403:
                raise QuotaExceededError("YouTube API квота исчерпана") from e
            raise

        _quota_used["search_list"] += 100
        logger.info("100 units (search.list) | total: %d", sum(_quota_used.values()))
        if sum(_quota_used.values()) > 8000:
            logger.warning("YouTube API quota > 8000 units — осторожно")

        ids = [item["id"]["videoId"] for item in response.get("items", [])]
        if not ids:
            break

        details = get_video_details(ids)
        for item in response.get("items", []):
            vid_id = item["id"]["videoId"]
            snippet = item["snippet"]
            detail = details.get(vid_id)
            if not detail:
                continue
            videos.append(VideoMeta(
                video_id=vid_id,
                title=snippet["title"],
                duration=detail["duration"],
                view_count=detail["view_count"],
                upload_date=snippet.get("publishedAt", "")[:10].replace("-", ""),
                channel=snippet["channelTitle"],
                url=f"https://youtube.com/watch?v={vid_id}",
            ))

        page_token = response.get("nextPageToken")
        remaining -= len(ids)
        if not page_token:
            break

    before = len(videos)
    videos = exclude_shorts(videos)
    filtered_count = before - len(videos)
    if filtered_count:
        logger.info("Filtered %d Shorts", filtered_count)

    return videos[:limit]


def get_video_details(video_ids: list[str]) -> dict:
    yt = get_youtube_client()
    result = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        try:
            resp = yt.videos().list(
                part="contentDetails,statistics",
                id=",".join(batch),
            ).execute()
        except HttpError:
            continue
        _quota_used["videos_list"] += 1  # videos.list costs 1 unit per call regardless of batch size
        for item in resp.get("items", []):
            vid_id = item["id"]
            duration_iso = item["contentDetails"]["duration"]
            result[vid_id] = {
                "duration": _parse_duration_iso(duration_iso),
                "view_count": int(item["statistics"].get("viewCount", 0)),
            }
    return result


def check_captions(video_ids: list[str]) -> dict[str, bool]:
    yt = get_youtube_client()
    result = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        try:
            resp = yt.videos().list(
                part="contentDetails",
                id=",".join(batch),
            ).execute()
        except HttpError:
            continue
        for item in resp.get("items", []):
            vid_id = item["id"]
            result[vid_id] = item["contentDetails"].get("caption") == "true"
    return result


class QuotaExceededError(Exception):
    pass
