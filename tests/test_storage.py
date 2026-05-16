"""
Phase 3 tests — core/storage.py
Run: venv/bin/python -m pytest tests/test_storage.py -v
"""
import sys, os, tempfile
sys.path.insert(0, '.')

import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Each test gets its own isolated DB and transcripts dir."""
    import config
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "TRANSCRIPTS_DIR", tmp_path / "transcripts")
    (tmp_path / "transcripts").mkdir()

    import core.storage as storage
    import importlib
    importlib.reload(storage)
    storage.init_db()
    yield storage


def _fake_result(video_id="test123"):
    class R:
        pass
    r = R()
    r.video_id = video_id
    r.method = "youtube_captions"
    r.language = "ru"
    r.duration_sec = 300
    r.status = "completed"
    return r


def test_init_db_idempotent(tmp_db):
    tmp_db.init_db()  # second call — no error


def test_is_cached_returns_false_for_unknown(tmp_db):
    assert tmp_db.is_cached("nonexistent") is False


def test_save_and_is_cached(tmp_db):
    result = _fake_result("vid001")
    tmp_db.save_transcript(result, "текст транскрипции", title="Test Video",
                           channel="Test Channel", upload_date="20240315")
    assert tmp_db.is_cached("vid001") is True


def test_save_transcript_creates_txt(tmp_db):
    import config
    result = _fake_result("vid002")
    path = tmp_db.save_transcript(result, "содержимое файла", title="Title",
                                  channel="Channel", upload_date="20240101")
    assert Path(path).exists()
    content = Path(path).read_text(encoding="utf-8")
    assert "vid002" in content
    assert "содержимое файла" in content
    assert "Заголовок:" in content


def test_save_transcript_no_duplicate(tmp_db):
    result = _fake_result("vid003")
    tmp_db.save_transcript(result, "текст 1", title="T", upload_date="20240101")
    tmp_db.save_transcript(result, "текст 2", title="T", upload_date="20240101")
    with tmp_db.get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM transcripts WHERE video_id='vid003'").fetchone()[0]
    assert count == 1


def test_txt_file_is_utf8(tmp_db):
    result = _fake_result("vid004")
    path = tmp_db.save_transcript(result, "Привет мир кириллица", title="Кириллица",
                                  channel="Канал", upload_date="20240101")
    content = Path(path).read_text(encoding="utf-8")
    assert "Привет" in content


def test_create_job_and_update_status(tmp_db):
    job_id = tmp_db.create_job("transcribe", videos=[{"video_id": v} for v in ["v1", "v2", "v3"]])
    assert isinstance(job_id, int)
    with tmp_db.get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM job_videos WHERE job_id=?", (job_id,)).fetchone()[0]
    assert count == 3

    tmp_db.update_video_status(job_id, "v1", "completed")
    with tmp_db.get_conn() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert job["completed"] == 1
