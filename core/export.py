import io
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import TRANSCRIPTS_DIR


def _safe_filename(s: str, max_len: int = 50) -> str:
    s = re.sub(r'[^\w\s\-]', '', s, flags=re.UNICODE)
    s = re.sub(r'\s+', '_', s.strip())
    return s[:max_len]


def export_to_obsidian(
    video_id: str,
    title: str,
    channel: str,
    text: str,
    upload_date: str,
    duration_sec: int,
    view_count: int,
    vault_path: str,
) -> Path:
    vault = Path(vault_path)
    channel_dir = vault / _safe_filename(channel or "unknown")
    channel_dir.mkdir(parents=True, exist_ok=True)

    date_str = upload_date
    if len(date_str) == 8 and date_str.isdigit():
        date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    elif not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")

    channel_slug = _safe_filename(channel or "unknown", 20).lower()
    filename = _safe_filename(title)[:50] + ".md"
    md_path = channel_dir / filename

    frontmatter = (
        "---\n"
        f"title: \"{title}\"\n"
        f"channel: \"{channel}\"\n"
        f"date: \"{date_str}\"\n"
        f"url: \"https://youtube.com/watch?v={video_id}\"\n"
        f"source: youtube\n"
        f"duration: {duration_sec}\n"
        f"views: {view_count}\n"
        f"tags: [youtube, транскрипция, {channel_slug}]\n"
        "---\n\n"
    )
    body = (
        f"# {title}\n\n"
        f"> Источник: [YouTube](https://youtube.com/watch?v={video_id}) · {channel} · {date_str}\n\n"
        "---\n\n"
        f"{text}\n"
    )

    md_path.write_text(frontmatter + body, encoding="utf-8")
    return md_path


def export_batch_zip(video_ids: list[str]) -> tuple[bytes, str]:
    """Returns (zip_bytes, filename)."""
    buf = io.BytesIO()
    date_str = datetime.now().strftime("%Y-%m-%d")
    zip_name = f"transcripts_{date_str}.zip"

    txt_files = list(TRANSCRIPTS_DIR.glob("*.txt"))
    selected = []
    for f in txt_files:
        for vid_id in video_ids:
            if vid_id in f.name:
                selected.append(f)
                break
    if not selected:
        selected = txt_files

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in selected:
            zf.writestr(f.name, f.read_text(encoding="utf-8"))

    return buf.getvalue(), zip_name
