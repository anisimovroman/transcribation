import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

YOUTUBE_API_KEY: str = os.getenv("YOUTUBE_API_KEY", "")
TRANSCRIPTS_DIR: Path = BASE_DIR / os.getenv("TRANSCRIPTS_DIR", "transcripts")
DB_PATH: Path = BASE_DIR / os.getenv("DB_PATH", "data/cache.db")
WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "medium")
WHISPER_DEVICE: str = os.getenv("WHISPER_DEVICE", "cpu")
MAX_WORKERS: int = int(os.getenv("MAX_WORKERS", "2"))
MAX_VIDEO_DURATION_SEC: int = int(os.getenv("MAX_VIDEO_DURATION_SEC", "10800"))
AUDIO_CHUNK_MINUTES: int = int(os.getenv("AUDIO_CHUNK_MINUTES", "15"))
HOST: str = os.getenv("HOST", "127.0.0.1")
PORT: int = int(os.getenv("PORT", "8000"))
DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
