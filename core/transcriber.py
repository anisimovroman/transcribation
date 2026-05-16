import asyncio
import glob
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
    RequestBlocked,
    CouldNotRetrieveTranscript,
)

from config import WHISPER_MODEL, WHISPER_DEVICE, MAX_VIDEO_DURATION_SEC, AUDIO_CHUNK_MINUTES

logger = logging.getLogger(__name__)

TEMP_DIR = Path("/tmp/transcribation")
CHUNK_OVERLAP_SEC = 10
YT_DLP_BIN = shutil.which("yt-dlp") or "yt-dlp"

# Apple Silicon MLX backend — 5-10x faster than CPU
try:
    import mlx_whisper as _mlx_whisper
    _USE_MLX = True
except ImportError:
    _USE_MLX = False

_MLX_MODEL_MAP = {
    "tiny":            "mlx-community/whisper-tiny-mlx",
    "base":            "mlx-community/whisper-base-mlx",
    "small":           "mlx-community/whisper-small-mlx",
    "medium":          "mlx-community/whisper-medium-mlx",
    "large":           "mlx-community/whisper-large-v3-mlx",
    "large-v3":        "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo":  "mlx-community/whisper-large-v3-turbo",
}

# Noise annotations Whisper sometimes inserts: [Music], [Applause], (background noise), etc.
_SEGMENT_NOISE = re.compile(r"\[(?!\d{2}:)[^\]]*\]|\([^\)]*\)")


def _mlx_repo() -> str:
    return _MLX_MODEL_MAP.get(WHISPER_MODEL, f"mlx-community/whisper-{WHISPER_MODEL}-mlx")


@dataclass
class TranscriptResult:
    video_id: str
    text: str
    method: str
    language: str
    duration_sec: int
    segments: Optional[list] = None  # list of {start: float, end: float, text: str}
    status: str = "completed"
    error: Optional[str] = None


def _fmt_ts(sec: float) -> str:
    s = int(sec)
    return f"[{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}]"


def segments_to_text(segments: list, with_timestamps: bool = False) -> str:
    """Format segments list into plain text or timestamped text."""
    if not segments:
        return ""
    if with_timestamps:
        lines = []
        for s in segments:
            cleaned = _SEGMENT_NOISE.sub("", s.get("text", "")).strip()
            if cleaned:
                lines.append(f"{_fmt_ts(s['start'])} {cleaned}")
        return "\n".join(lines)
    return " ".join(s["text"].strip() for s in segments if s.get("text", "").strip())


_whisper_model = None


def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        if _USE_MLX:
            repo = _mlx_repo()
            logger.info("Загрузка MLX Whisper '%s' на Apple Silicon GPU...", repo)
            import numpy as np
            # Warm up: runs model download + Metal compilation on 1 sec of silence
            _mlx_whisper.transcribe(
                np.zeros(16000, dtype=np.float32),
                path_or_hf_repo=repo,
                verbose=False,
            )
            _whisper_model = repo
            logger.info("MLX Whisper готов (GPU)")
        else:
            from faster_whisper import WhisperModel
            logger.info("Загрузка Whisper '%s' на '%s'...", WHISPER_MODEL, WHISPER_DEVICE)
            _whisper_model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type="int8")
            logger.info("Whisper загружен")
    return _whisper_model


_yt_api_instance: Optional[YouTubeTranscriptApi] = None


def _make_yt_api() -> YouTubeTranscriptApi:
    global _yt_api_instance
    if _yt_api_instance is None:
        _yt_api_instance = YouTubeTranscriptApi()
    return _yt_api_instance


def get_youtube_captions(video_id: str, lang: str = "ru") -> Optional[tuple[list, str]]:
    try:
        api = _make_yt_api()
        transcript = api.fetch(
            video_id,
            languages=[lang, "ru", "ru-auto", "en", "en-auto"],
        )
        segs = [
            {"start": seg.start, "end": seg.start + seg.duration, "text": seg.text}
            for seg in transcript
        ]
        used_lang = getattr(transcript, "language_code", lang)
        logger.info("Субтитры OK lang=%s video=%s segs=%d", used_lang, video_id, len(segs))
        return segs, used_lang
    except RequestBlocked:
        logger.warning("YouTube заблокировал запрос субтитров для %s — Whisper fallback", video_id)
        return None
    except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable, CouldNotRetrieveTranscript):
        return None
    except Exception as e:
        logger.warning("Ошибка субтитров %s: %s — Whisper fallback", video_id, e)
        return None


async def download_audio(video_id: str) -> Path:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    out_template = str(TEMP_DIR / f"{video_id}.%(ext)s")

    existing = [p for p in TEMP_DIR.glob(f"{video_id}.*") if p.suffix != ".wav"]
    if existing:
        logger.info("Аудио уже скачано: %s", existing[0])
        return existing[0]

    url = f"https://youtube.com/watch?v={video_id}"
    base_cmd = [YT_DLP_BIN, "-f", "bestaudio", "--no-playlist", "-o", out_template, "--no-color"]

    # Try without cookies first; if it fails, retry with each installed browser's cookies.
    # This avoids hardcoding a specific browser (Chrome may not be installed).
    BROWSERS = ["chrome", "firefox", "edge", "brave", "safari", "chromium"]
    attempts = [base_cmd + [url]]
    for browser in BROWSERS:
        attempts.append(base_cmd + ["--cookies-from-browser", browser, url])

    last_err = ""
    for attempt_cmd in attempts:
        proc = await asyncio.create_subprocess_exec(
            *attempt_cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"yt-dlp таймаут (5 мин) для {video_id}")

        found = [p for p in TEMP_DIR.glob(f"{video_id}.*") if p.suffix != ".wav"]
        if found:
            logger.info("Аудио скачано: %s (cmd: %s)", found[0], " ".join(attempt_cmd[-3:]))
            return found[0]

        last_err = stderr_bytes.decode("utf-8", errors="replace")[:400] if stderr_bytes else ""
        # If error is not auth-related, don't bother trying other browsers
        if "Sign in" not in last_err and "bot" not in last_err.lower() and "403" not in last_err:
            break

    raise RuntimeError(f"yt-dlp не скачал аудио для {video_id}: {last_err}")


def convert_to_wav(audio_path: Path) -> Path:
    wav_path = audio_path.with_suffix(".wav")
    subprocess.run(
        ["ffmpeg", "-i", str(audio_path), "-ar", "16000", "-ac", "1", "-f", "wav",
         str(wav_path), "-y", "-loglevel", "error"],
        capture_output=True, check=True,
    )
    audio_path.unlink(missing_ok=True)
    logger.info("Конвертировано в WAV: %s", wav_path)
    return wav_path


def split_audio(wav_path: Path, duration_sec: int) -> list[Path]:
    chunk_sec = AUDIO_CHUNK_MINUTES * 60
    chunks: list[Path] = []
    start = 0
    idx = 0
    while start < duration_sec:
        chunk_path = wav_path.parent / f"{wav_path.stem}_chunk_{idx:03d}.wav"
        length = chunk_sec + CHUNK_OVERLAP_SEC
        subprocess.run(
            ["ffmpeg", "-i", str(wav_path), "-ss", str(start), "-t", str(length),
             str(chunk_path), "-y", "-loglevel", "error"],
            capture_output=True, check=True,
        )
        chunks.append(chunk_path)
        start += chunk_sec
        idx += 1
    return chunks


def transcribe_single(wav_path: Path) -> tuple[list, str]:
    """Returns (segments, language). Each segment: {start, end, text}."""
    model = get_whisper_model()
    if _USE_MLX:
        result = _mlx_whisper.transcribe(
            str(wav_path),
            path_or_hf_repo=model,
            verbose=False,
        )
        segs = [
            {"start": s["start"], "end": s["end"], "text": s["text"]}
            for s in result.get("segments", [])
        ]
        return segs, result.get("language", "ru")
    else:
        raw_segments, info = model.transcribe(str(wav_path), beam_size=5, vad_filter=True)
        segs = [{"start": s.start, "end": s.end, "text": s.text} for s in raw_segments]
        return segs, info.language


def transcribe_chunks(chunks: list[Path]) -> tuple[list, str]:
    """Transcribe chunked audio files, merging segments with corrected timestamps."""
    chunk_sec = AUDIO_CHUNK_MINUTES * 60
    all_segs: list[dict] = []
    language = "ru"
    for i, chunk in enumerate(chunks):
        is_last = (i == len(chunks) - 1)
        segs, lang = transcribe_single(chunk)
        language = lang
        offset = i * chunk_sec
        for s in segs:
            # Skip overlap zone at end of each chunk (those seconds are covered by next chunk)
            if not is_last and s["start"] >= chunk_sec:
                continue
            all_segs.append({
                "start": s["start"] + offset,
                "end": s["end"] + offset,
                "text": s["text"],
            })
        chunk.unlink(missing_ok=True)
    return all_segs, language


def cleanup_temp_files(video_id: str):
    patterns = [
        str(TEMP_DIR / f"{video_id}.*"),
        str(TEMP_DIR / f"{video_id}_chunk_*.wav"),
    ]
    for pattern in patterns:
        for path in glob.glob(pattern):
            try:
                os.remove(path)
            except OSError:
                pass


async def transcribe_video(
    video_id: str,
    title: str,
    duration_sec: int = 0,
) -> TranscriptResult:
    if duration_sec > MAX_VIDEO_DURATION_SEC:
        hours = MAX_VIDEO_DURATION_SEC // 3600
        return TranscriptResult(
            video_id=video_id, text="", method="", language="",
            duration_sec=duration_sec, status="failed",
            error=f"Видео длиннее {hours} часов — пропущено",
        )

    try:
        # 1. Try YouTube captions
        captions = await asyncio.to_thread(get_youtube_captions, video_id)
        if captions:
            segs, language = captions
            return TranscriptResult(
                video_id=video_id,
                text=segments_to_text(segs),
                method="youtube_captions",
                language=language,
                duration_sec=duration_sec,
                segments=segs,
            )

        # 2. Download audio
        audio_path = await download_audio(video_id)

        # 3. Convert to WAV
        wav_path = await asyncio.to_thread(convert_to_wav, audio_path)

        # 4. Transcribe
        chunk_threshold = 45 * 60
        if duration_sec > chunk_threshold:
            chunks = await asyncio.to_thread(split_audio, wav_path, duration_sec)
            segs, language = await asyncio.to_thread(transcribe_chunks, chunks)
            wav_path.unlink(missing_ok=True)
        else:
            segs, language = await asyncio.to_thread(transcribe_single, wav_path)
            wav_path.unlink(missing_ok=True)

        backend = f"mlx_{WHISPER_MODEL}_gpu" if _USE_MLX else f"whisper_{WHISPER_MODEL}_cpu"
        return TranscriptResult(
            video_id=video_id,
            text=segments_to_text(segs),
            method=backend,
            language=language,
            duration_sec=duration_sec,
            segments=segs,
        )

    except Exception as e:
        logger.error("Ошибка транскрибации %s: %s", video_id, e, exc_info=True)
        return TranscriptResult(
            video_id=video_id, text="", method="", language="",
            duration_sec=duration_sec, status="failed", error=str(e),
        )
    finally:
        cleanup_temp_files(video_id)
