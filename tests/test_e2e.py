"""
E2E tests — t512, t513
Full pipeline: API → transcribe → .txt → cache

Run: venv/bin/python -m pytest tests/test_e2e.py -v -s
"""
import sys, time, asyncio
sys.path.insert(0, '.')

import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from pathlib import Path


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "TRANSCRIPTS_DIR", tmp_path / "transcripts")
    (tmp_path / "transcripts").mkdir()

    import core.storage as storage
    import importlib
    importlib.reload(storage)
    storage.init_db()

    import core.export as export_mod
    importlib.reload(export_mod)

    # Patch routes _settings so each test gets its own isolated transcripts dir
    import api.routes as routes_mod
    monkeypatch.setitem(routes_mod._settings, "transcripts_dir", str(tmp_path / "transcripts"))

    yield {"tmp": tmp_path, "storage": storage}


@pytest.fixture
def client(isolated_env):
    from main import app
    import core.storage as storage
    storage.init_db()
    return TestClient(app, raise_server_exceptions=False)


def _mock_channel_videos(channel_url, limit=None, **kwargs):
    from core.youtube import VideoMeta
    return [
        VideoMeta(f"vid{i:03d}", f"Test Video {i}", 1200, 50000 + i * 1000,
                  "20240315", "Test Channel", f"https://youtube.com/watch?v=vid{i:03d}")
        for i in range(1, (limit or 3) + 1)
    ]


def _mock_search_videos(query, order="relevance", limit=20, **kwargs):
    from core.youtube import VideoMeta
    vids = [
        VideoMeta("abc11111111", f"Search Result 1 — {query}", 1800, 2_000_000,
                  "20240201", "Channel A", "https://youtube.com/watch?v=abc11111111"),
        VideoMeta("def22222222", f"Search Result 2 — {query}", 900, 500_000,
                  "20240101", "Channel B", "https://youtube.com/watch?v=def22222222"),
    ]
    return vids[:limit]


async def _mock_transcribe_video(video_id, title, duration_sec=0):
    from core.transcriber import TranscriptResult
    return TranscriptResult(
        video_id=video_id,
        text=f"Это транскрипция видео {video_id}. Lorem ipsum dolor sit amet.",
        method="youtube_captions",
        language="ru",
        duration_sec=duration_sec or 1200,
    )


def _wait_for_job(client, job_id, timeout=10):
    """Poll /api/results until job completes (for TestClient, background tasks run inline)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/progress/{job_id}")
        # TestClient SSE: just check DB
        import core.storage as storage
        with storage.get_conn() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job and job["status"] in ("completed", "failed"):
                return dict(job)
        time.sleep(0.2)
    return None


# ══════════════════════════════════════════════════════════════
# t512 — Scenario A: channel → transcribe → .txt
# ══════════════════════════════════════════════════════════════

def test_e2e_channel_to_txt(client, isolated_env):
    """t512: канал → 3 видео → транскрибация → 3 .txt файла"""

    with patch("api.routes.get_channel_videos", side_effect=_mock_channel_videos), \
         patch("api.routes.transcribe_video", new=_mock_transcribe_video):

        # Step 1: Get channel videos
        r = client.post("/api/channel", json={"channel_url": "@testchannel", "limit": 3})
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 3
        video_ids = [v["video_id"] for v in data["videos"]]
        assert len(video_ids) == 3

        # Step 2: Transcribe
        r = client.post("/api/transcribe", json={"videos": data["videos"]})
        assert r.status_code == 200
        job_id = r.json()["job_id"]
        assert isinstance(job_id, int)

        # BackgroundTasks completes before client.post() returns in TestClient
        import core.storage as storage

        # Step 4: Verify
        with storage.get_conn() as conn:
            completed = conn.execute(
                "SELECT COUNT(*) FROM transcripts WHERE status='completed'"
            ).fetchone()[0]

        transcripts_dir = isolated_env["tmp"] / "transcripts"
        txt_files = list(transcripts_dir.glob("*.txt"))

        assert completed == 3, f"Expected 3 completed transcripts, got {completed}"
        assert len(txt_files) == 3, f"Expected 3 .txt files, got {len(txt_files)}"

        for f in txt_files:
            content = f.read_text(encoding="utf-8")
            assert "Заголовок:" in content
            assert "URL:" in content
            assert len(content) > 100


# ══════════════════════════════════════════════════════════════
# t513 — Scenario B: search → filter → .txt + cache
# ══════════════════════════════════════════════════════════════

def test_e2e_search_to_txt(client, isolated_env):
    """t513: поиск → 2 видео → транскрибация → 2 .txt"""

    with patch("api.routes.search_videos", side_effect=_mock_search_videos), \
         patch("api.routes.transcribe_video", new=_mock_transcribe_video):

        # Step 1: Search with viewCount sort
        r = client.post("/api/search", json={
            "query": "machine learning", "order": "viewCount", "limit": 5
        })
        assert r.status_code == 200
        data = r.json()
        videos = data["videos"]
        assert len(videos) >= 2

        # Verify sorted by view_count (mock returns descending)
        if len(videos) >= 2:
            assert videos[0]["view_count"] >= videos[1]["view_count"]

        # Step 2: Transcribe first 2
        r = client.post("/api/transcribe", json={"videos": videos[:2]})
        assert r.status_code == 200
        job_id = r.json()["job_id"]

        import core.storage as storage

        transcripts_dir = isolated_env["tmp"] / "transcripts"
        txt_files = list(transcripts_dir.glob("*.txt"))
        assert len(txt_files) == 2, f"Expected 2 .txt files, got {len(txt_files)}"

        # Step 3: Test cache — repeat transcription should be fast
        start = time.time()
        r = client.post("/api/transcribe", json={"videos": videos[:2]})
        job_id2 = r.json()["job_id"]
        elapsed = time.time() - start
        assert elapsed < 5, f"Cache replay took {elapsed:.1f}s — должен быть быстрым"

        # Still only 2 .txt files (cache, not duplicates)
        txt_files_after = list(transcripts_dir.glob("*.txt"))
        assert len(txt_files_after) == 2


# ══════════════════════════════════════════════════════════════
# Misc API tests
# ══════════════════════════════════════════════════════════════

def test_results_api_empty(client):
    r = client.get("/api/results")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 0
    assert data["results"] == []


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_channel_invalid_returns_404(client):
    with patch("api.routes.get_channel_videos", return_value=[]):
        r = client.post("/api/channel", json={"channel_url": "@nonexistent_xyz"})
        assert r.status_code == 404


def test_transcribe_empty_list_returns_422(client):
    r = client.post("/api/transcribe", json={"videos": []})
    assert r.status_code == 422
