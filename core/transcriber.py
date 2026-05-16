import asyncio
import glob
import logging
import os
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

def _mlx_repo() -> str:
    return _MLX_MODEL_MAP.get(WHISPER_MODEL, f"mlx-community/whisper-{WHISPER_MODEL}-mlx")


@dataclass
class TranscriptResult:
    video_id: str
    text: str
    method: str
    language: str
    duration_sec: int
    status: str = "completed"
    error: Optional[str] = None


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
    """Return a shared YouTubeTranscriptApi instance."""
    global _yt_api_instance
    if _yt_api_instance is None:
        _yt_api_instance = YouTubeTranscriptApi()
    return _yt_api_instance


def get_youtube_captions(video_id: str, lang: str = "ru") -> Optional[tuple[str, str]]:
    try:
        api = _make_yt_api()
        transcript = api.fetch(
            video_id,
            languages=[lang, "ru", "ru-auto", "en", "en-auto"],
        )
        text = " ".join(seg.text for seg in transcript)
        used_lang = getattr(transcript, "language_code", lang)
        logger.info("Субтитры OK lang=%s video=%s len=%d", used_lang, video_id, len(text))
        return text, used_lang
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
    # Chrome cookies + remote EJS components required for modern YouTube
    cmd = [
        YT_DLP_BIN, "-f", "bestaudio", "--no-playlist",
        "--cookies-from-browser", "chrome",
        "--remote-components", "ejs:github",
        "-o", out_template, "--no-color",
        url,
    ]
    logger.info("Скачиваю аудио: %s", video_id)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"yt-dlp таймаут (5 мин) для {video_id}")

    found = [p for p in TEMP_DIR.glob(f"{video_id}.*") if p.suffix != ".wav"]
    if not found:
        err_msg = stderr.decode("utf-8", errors="replace")[:400] if stderr else "нет деталей"
        raise RuntimeError(f"yt-dlp не скачал аудио для {video_id}: {err_msg}")
    return found[0]


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


def transcribe_single(wav_path: Path) -> tuple[str, str]:
    model = get_whisper_model()
    if _USE_MLX:
        result = _mlx_whisper.transcribe(
            str(wav_path),
            path_or_hf_repo=model,
            verbose=False,
        )
        return result["text"].strip(), result.get("language", "ru")
    else:
        segments, info = model.transcribe(str(wav_path), beam_size=5, vad_filter=True)
        text = " ".join(s.text.strip() for s in segments)
        return text, info.language


def _deduplicate_overlap(a: str, b: str) -> str:
    import difflib
    words_a = a.split()
    words_b = b.split()
    overlap = min(50, len(words_a), len(words_b))
    for size in range(overlap, 0, -1):
        if words_a[-size:] == words_b[:size]:
            return a + " " + " ".join(words_b[size:])
    return a + " " + b


def transcribe_chunks(chunks: list[Path]) -> tuple[str, str]:
    combined = ""
    language = "ru"
    for i, chunk in enumerate(chunks):
        logger.info("Chunk %d/%d...", i + 1, len(chunks))
        text, lang = transcribe_single(chunk)
        language = lang
        combined = _deduplicate_overlap(combined, text) if combined else text
        chunk.unlink(missing_ok=True)
    return combined.strip(), language


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
        # 1. Try YouTube captions (non-blocking)
        captions = await asyncio.to_thread(get_youtube_captions, video_id)
        if captions:
            text, language = captions
            return TranscriptResult(
                video_id=video_id, text=text, method="youtube_captions",
                language=language, duration_sec=duration_sec,
            )

        # 2. Download audio (async subprocess, non-blocking)
        audio_path = await download_audio(video_id)

        # 3. Convert to WAV (blocking, run in thread)
        wav_path = await asyncio.to_thread(convert_to_wav, audio_path)

        # 4. Transcribe (blocking CPU work, run in thread)
        chunk_threshold = 45 * 60
        if duration_sec > chunk_threshold:
            chunks = await asyncio.to_thread(split_audio, wav_path, duration_sec)
            text, language = await asyncio.to_thread(transcribe_chunks, chunks)
            wav_path.unlink(missing_ok=True)
        else:
            text, language = await asyncio.to_thread(transcribe_single, wav_path)
            wav_path.unlink(missing_ok=True)

        backend = f"mlx_{WHISPER_MODEL}_gpu" if _USE_MLX else f"whisper_{WHISPER_MODEL}_cpu"
        return TranscriptResult(
            video_id=video_id, text=text, method=backend,
            language=language, duration_sec=duration_sec,
        )

    except Exception as e:
        logger.error("Ошибка транскрибации %s: %s", video_id, e, exc_info=True)
        return TranscriptResult(
            video_id=video_id, text="", method="", language="",
            duration_sec=duration_sec, status="failed", error=str(e),
        )
    finally:
        cleanup_temp_files(video_id)
