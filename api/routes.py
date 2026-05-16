import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, Field
import json

from core.youtube import get_channel_videos, search_videos, QuotaExceededError
from core.transcriber import transcribe_video
from core.storage import (
    get_conn, is_cached, get_cached, save_transcript,
    create_job, update_video_status, get_pending_videos,
    search_transcripts, get_job_status,
)
from core.postprocess import postprocess
from core.export import export_to_obsidian, export_batch_zip
from config import MAX_WORKERS, TRANSCRIPTS_DIR

logger = logging.getLogger(__name__)
router = APIRouter()

# ─── Runtime settings (mutable, reset on server restart) ────────
_settings = {
    "workers": MAX_WORKERS,
    "transcripts_dir": str(TRANSCRIPTS_DIR),
    "mode": "balanced",
}

MODES = {
    "safe":     {"workers": 1,  "label": "Экономный"},
    "balanced": {"workers": 2,  "label": "Стандартный"},
    "fast":     {"workers": 4,  "label": "Быстрый"},
}


# ─── Request schemas ────────────────────────────────────────────

class ChannelRequest(BaseModel):
    channel_url: str
    limit: int = Field(default=50, ge=1, le=200)
    exclude_shorts: bool = True
    min_duration_sec: int = Field(default=0, ge=0)
    max_duration_sec: Optional[int] = None


class SearchRequest(BaseModel):
    query: str
    order: str = "relevance"
    duration_filter: str = "any"
    date_filter: Optional[str] = None
    limit: int = Field(default=50, ge=1, le=200)


class VideoMeta(BaseModel):
    video_id: str
    title: str = ""
    channel: str = ""
    duration_sec: int = 0
    view_count: int = 0
    upload_date: str = ""


class TranscribeRequest(BaseModel):
    videos: list[VideoMeta] = Field(min_length=1, max_length=200)
    out_dir: Optional[str] = None


# ─── Routes ─────────────────────────────────────────────────────

@router.get("/health")
async def health():
    return {"status": "ok"}


# ─── Settings ───────────────────────────────────────────────────

class SettingsRequest(BaseModel):
    mode: Optional[str] = None
    transcripts_dir: Optional[str] = None


@router.get("/settings")
async def get_settings():
    return {
        **_settings,
        "modes": MODES,
        "default_transcripts_dir": str(TRANSCRIPTS_DIR),
    }


@router.post("/settings")
async def update_settings(req: SettingsRequest):
    if req.mode is not None:
        if req.mode not in MODES:
            raise HTTPException(status_code=400, detail=f"Неизвестный режим: {req.mode}")
        _settings["mode"] = req.mode
        _settings["workers"] = MODES[req.mode]["workers"]

    if req.transcripts_dir is not None:
        if req.transcripts_dir == "":
            _settings["transcripts_dir"] = str(TRANSCRIPTS_DIR)
        else:
            path = Path(req.transcripts_dir).expanduser().resolve()
            try:
                path.mkdir(parents=True, exist_ok=True)
                test_file = path / ".write_test"
                test_file.touch()
                test_file.unlink()
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Папка недоступна: {e}")
            _settings["transcripts_dir"] = str(path)

    return _settings


@router.post("/validate-path")
async def validate_path(body: dict):
    raw = body.get("path", "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Путь не указан")
    path = Path(raw).expanduser().resolve()
    try:
        path.mkdir(parents=True, exist_ok=True)
        test_file = path / ".write_test"
        test_file.touch()
        test_file.unlink()
        return {"ok": True, "resolved": str(path)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


def _open_folder_dialog(title: str) -> str:
    """Open a native OS folder picker and return the chosen path, or '' if cancelled."""
    if sys.platform == "darwin":
        script = f'POSIX path of (choose folder with prompt "{title}")'
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""

    if sys.platform == "win32":
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
            f'$d.Description="{title}";'
            "$d.ShowNewFolderButton=$true;"
            "if($d.ShowDialog() -eq 'OK'){$d.SelectedPath}"
        )
        r = subprocess.run(["powershell", "-Command", ps], capture_output=True, text=True)
        return r.stdout.strip()

    # Linux — try zenity, fall back to kdialog
    for cmd in [
        ["zenity", "--file-selection", "--directory", f"--title={title}"],
        ["kdialog", "--getexistingdirectory", os.path.expanduser("~"), f"--title={title}"],
    ]:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except FileNotFoundError:
            continue
    return ""


@router.post("/pick-folder")
async def pick_folder(body: dict = {}):
    title = body.get("title", "Выберите папку")
    try:
        path = await asyncio.to_thread(_open_folder_dialog, title)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not path:
        raise HTTPException(status_code=204, detail="Отменено")
    resolved = str(Path(path).expanduser().resolve())
    return {"path": resolved}


@router.post("/channel")
async def channel_videos(req: ChannelRequest):
    try:
        videos = get_channel_videos(
            req.channel_url,
            limit=req.limit,
            exclude_shorts_flag=req.exclude_shorts,
            min_duration_sec=req.min_duration_sec,
            max_duration_sec=req.max_duration_sec,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if not videos:
        raise HTTPException(status_code=404, detail="Канал не найден или видео отсутствуют")
    return {"videos": [v.__dict__ for v in videos], "total": len(videos)}


@router.post("/search")
async def search(req: SearchRequest):
    try:
        videos = search_videos(
            query=req.query,
            order=req.order,
            duration_filter=req.duration_filter,
            date_filter=req.date_filter,
            limit=req.limit,
        )
    except QuotaExceededError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"videos": [v.__dict__ for v in videos], "total": len(videos)}


@router.post("/transcribe")
async def start_transcribe(req: TranscribeRequest, background_tasks: BackgroundTasks):
    videos_dicts = [v.model_dump() for v in req.videos]
    job_id = create_job("transcribe", videos=videos_dicts)
    background_tasks.add_task(_run_job, job_id, videos_dicts, req.out_dir)
    return {"job_id": job_id, "total": len(req.videos), "out_dir": req.out_dir or _settings["transcripts_dir"]}


@router.get("/progress/{job_id}")
async def progress(job_id: int):
    async def event_stream():
        while True:
            status = get_job_status(job_id)
            if not status:
                yield f"data: {json.dumps({'error': 'job not found'})}\n\n"
                break

            payload = {
                "total": status["total"],
                "completed": status["completed"],
                "failed": status["failed"],
                "status": status["status"],
                "videos": status["videos"],
            }
            yield f"data: {json.dumps(payload)}\n\n"

            if status["status"] in ("completed", "failed"):
                break
            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


async def _run_job(job_id: int, videos: list[dict], out_dir: Optional[str] = None):
    sem = asyncio.Semaphore(_settings["workers"])
    save_dir = out_dir or _settings["transcripts_dir"] or None

    async def process_one(v: dict):
        vid_id = v["video_id"]
        async with sem:
            try:
                update_video_status(job_id, vid_id, "processing")

                if is_cached(vid_id):
                    logger.info("Кэш: %s", vid_id)
                    update_video_status(job_id, vid_id, "completed")
                    return

                result = await transcribe_video(
                    vid_id,
                    title=v.get("title", vid_id),
                    duration_sec=v.get("duration_sec", 0),
                )

                if result.status == "completed":
                    text = postprocess(result.text, result.method, result.language)
                    save_transcript(
                        result,
                        text,
                        title=v.get("title", vid_id),
                        channel=v.get("channel", ""),
                        view_count=v.get("view_count", 0),
                        upload_date=v.get("upload_date", ""),
                        out_dir=save_dir,
                    )
                    update_video_status(job_id, vid_id, "completed")
                    logger.info("Saved: %s (%s)", vid_id, result.method)
                else:
                    update_video_status(job_id, vid_id, "failed", result.error or "")
                    logger.error("Failed: %s — %s", vid_id, result.error)

            except Exception as e:
                logger.error("Job %d video %s exception: %s", job_id, vid_id, e, exc_info=True)
                update_video_status(job_id, vid_id, "failed", str(e)[:200])

    await asyncio.gather(*[process_one(v) for v in videos])

    with get_conn() as conn:
        conn.execute("UPDATE jobs SET status='completed' WHERE id=?", (job_id,))


# ─── Results ────────────────────────────────────────────────────

@router.get("/results")
async def results(
    q: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
):
    if q:
        return search_transcripts(q, page=page, per_page=per_page)
    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM transcripts WHERE status='completed'"
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT video_id, title, channel, duration_sec, method, language, "
            "upload_date, txt_path, created_at "
            "FROM transcripts WHERE status='completed' ORDER BY created_at DESC "
            "LIMIT ? OFFSET ?",
            (per_page, (page - 1) * per_page),
        ).fetchall()
    return {"results": [dict(r) for r in rows], "total": total, "page": page}


@router.get("/transcripts/{video_id}")
async def get_transcript(video_id: str):
    row = get_cached(video_id)
    if not row:
        raise HTTPException(status_code=404, detail="Транскрипция не найдена")
    return row


# ─── Time estimate ──────────────────────────────────────────────

class EstimateVideo(BaseModel):
    video_id: str
    duration_sec: int = 1800


class EstimateRequest(BaseModel):
    videos: list[EstimateVideo]


@router.post("/estimate")
async def estimate_time(req: EstimateRequest):
    CAPTION_RATE = 0.65
    CAPTION_SEC = 3
    WHISPER_RATIO = 0.5
    WORKERS = 2

    cached_ids: set[str] = set()
    with get_conn() as conn:
        for v in req.videos:
            row = conn.execute(
                "SELECT 1 FROM transcripts WHERE video_id=? AND status='completed'",
                (v.video_id,),
            ).fetchone()
            if row:
                cached_ids.add(v.video_id)

    non_cached = [v for v in req.videos if v.video_id not in cached_ids]
    n = len(non_cached)
    n_caption = round(n * CAPTION_RATE)
    n_whisper = n - n_caption
    avg_dur = sum(v.duration_sec for v in non_cached) / n if n else 0

    caption_time = n_caption * CAPTION_SEC
    whisper_time = n_whisper * avg_dur * WHISPER_RATIO
    total_sec = int((caption_time + whisper_time) / WORKERS)
    total_dur = sum(v.duration_sec for v in non_cached)
    best_sec = int((n * CAPTION_SEC) / WORKERS)
    worst_sec = int((total_dur * WHISPER_RATIO) / WORKERS)

    return {
        "total_sec": total_sec,
        "best_sec": best_sec,
        "worst_sec": worst_sec,
        "cached_count": len(cached_ids),
        "caption_count": n_caption,
        "whisper_count": n_whisper,
        "workers": WORKERS,
    }


# ─── Export ─────────────────────────────────────────────────────

class ObsidianExportRequest(BaseModel):
    video_ids: list[str]
    vault_path: str


class ZipExportRequest(BaseModel):
    video_ids: list[str] = []


@router.post("/export/obsidian")
async def export_obsidian(req: ObsidianExportRequest):
    exported = []
    skipped = []
    for vid_id in req.video_ids:
        row = get_cached(vid_id)
        if not row:
            skipped.append(vid_id)
            continue
        path = export_to_obsidian(
            video_id=vid_id,
            title=row["title"] or vid_id,
            channel=row["channel"] or "",
            text=row["text"] or "",
            upload_date=row["upload_date"] or "",
            duration_sec=row["duration_sec"] or 0,
            view_count=row["view_count"] or 0,
            vault_path=req.vault_path,
        )
        exported.append(str(path))
    return {"exported": len(exported), "paths": exported, "skipped": skipped}


@router.post("/export/zip")
async def export_zip(req: ZipExportRequest):
    zip_bytes, zip_name = export_batch_zip(req.video_ids)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )
