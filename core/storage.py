import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import DB_PATH, TRANSCRIPTS_DIR


@contextmanager
def get_conn():
    """Context manager that opens, yields, commits/rolls back, and CLOSES the connection."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection):
    """Add missing columns to existing tables without data loss."""
    existing = {
        row[0] for row in conn.execute(
            "SELECT name FROM pragma_table_info('job_videos')"
        ).fetchall()
    }
    additions = {
        "channel":      "TEXT DEFAULT ''",
        "duration_sec": "INTEGER DEFAULT 0",
        "view_count":   "INTEGER DEFAULT 0",
        "upload_date":  "TEXT DEFAULT ''",
        "error_msg":    "TEXT DEFAULT ''",
    }
    for col, col_def in additions.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE job_videos ADD COLUMN {col} {col_def}")

    # Add error_msg to job_videos if missing (legacy)
    tr_existing = {
        row[0] for row in conn.execute(
            "SELECT name FROM pragma_table_info('transcripts')"
        ).fetchall()
    }
    if "text" not in tr_existing:
        conn.execute("ALTER TABLE transcripts ADD COLUMN text TEXT DEFAULT ''")

    # Recreate FTS if it uses old content-table style
    old_fts = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='transcripts_fts'"
    ).fetchone()
    if old_fts and "content=" in (old_fts[0] or ""):
        conn.execute("DROP TABLE IF EXISTS transcripts_fts")


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS transcripts (
                video_id     TEXT PRIMARY KEY,
                title        TEXT NOT NULL DEFAULT '',
                channel      TEXT DEFAULT '',
                duration_sec INTEGER DEFAULT 0,
                method       TEXT DEFAULT '',
                language     TEXT DEFAULT '',
                text         TEXT DEFAULT '',
                txt_path     TEXT DEFAULT '',
                view_count   INTEGER DEFAULT 0,
                upload_date  TEXT DEFAULT '',
                created_at   TEXT DEFAULT (datetime('now')),
                status       TEXT DEFAULT 'pending'
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                type       TEXT NOT NULL,
                query      TEXT DEFAULT '',
                total      INTEGER DEFAULT 0,
                completed  INTEGER DEFAULT 0,
                failed     INTEGER DEFAULT 0,
                status     TEXT DEFAULT 'running',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS job_videos (
                job_id      INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
                video_id    TEXT NOT NULL,
                position    INTEGER NOT NULL,
                status      TEXT DEFAULT 'pending',
                title       TEXT DEFAULT '',
                channel     TEXT DEFAULT '',
                duration_sec INTEGER DEFAULT 0,
                view_count  INTEGER DEFAULT 0,
                upload_date TEXT DEFAULT '',
                error_msg   TEXT DEFAULT '',
                PRIMARY KEY (job_id, video_id)
            );

            -- Standalone FTS5 (not content table — updated explicitly on save)
            CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts
            USING fts5(video_id UNINDEXED, title, channel, text, tokenize='unicode61');
        """)
        _migrate(conn)


def is_cached(video_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM transcripts WHERE video_id=? AND status='completed'",
            (video_id,),
        ).fetchone()
    return row is not None


def get_cached(video_id: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM transcripts WHERE video_id=? AND status='completed'",
            (video_id,),
        ).fetchone()
    return dict(row) if row else None


def save_txt(
    video_id: str, title: str, channel: str, text: str,
    method: str, upload_date: str,
    out_dir: Optional[str] = None,
) -> Path:
    safe_channel = "".join(
        c if c.isalnum() or c in "_ " else "_"
        for c in (channel or "unknown")
    )[:30].strip("_") or "unknown"

    date_str = (upload_date or "")[:10]
    if len(date_str) == 8 and date_str.isdigit():
        date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")

    filename = f"{date_str}_{safe_channel}_{video_id}.txt"
    base = Path(out_dir) if out_dir else TRANSCRIPTS_DIR
    base.mkdir(parents=True, exist_ok=True)
    path = base / filename

    header = (
        f"Заголовок: {title}\n"
        f"Канал: {channel}\n"
        f"Дата: {date_str}\n"
        f"URL: https://youtube.com/watch?v={video_id}\n"
        f"Метод: {method}\n"
        f"{'─' * 60}\n\n"
    )
    path.write_text(header + text, encoding="utf-8")
    return path


def save_transcript(
    result,
    text: str,
    title: str = "",
    channel: str = "",
    view_count: int = 0,
    upload_date: str = "",
    out_dir: Optional[str] = None,
) -> Path:
    final_title = title or result.video_id
    txt_path = save_txt(
        video_id=result.video_id,
        title=final_title,
        channel=channel,
        text=text,
        method=result.method,
        upload_date=upload_date,
        out_dir=out_dir,
    )
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO transcripts
               (video_id, title, channel, duration_sec, method, language,
                text, txt_path, view_count, upload_date, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed')""",
            (result.video_id, final_title, channel,
             result.duration_sec, result.method, result.language,
             text, str(txt_path), view_count, upload_date),
        )
        # Sync FTS5 index
        conn.execute(
            "INSERT OR REPLACE INTO transcripts_fts(video_id, title, channel, text) "
            "VALUES (?, ?, ?, ?)",
            (result.video_id, final_title, channel, text),
        )
    return txt_path


def create_job(job_type: str, query: str = "", videos: list[dict] = None) -> int:
    videos = videos or []
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO jobs (type, query, total, status) VALUES (?, ?, ?, 'running')",
            (job_type, query, len(videos)),
        )
        job_id = cur.lastrowid
        for pos, v in enumerate(videos):
            vid_id = v["video_id"] if isinstance(v, dict) else v
            v_title = v.get("title", "") if isinstance(v, dict) else ""
            v_channel = v.get("channel", "") if isinstance(v, dict) else ""
            v_dur = v.get("duration_sec", 0) if isinstance(v, dict) else 0
            v_views = v.get("view_count", 0) if isinstance(v, dict) else 0
            v_date = v.get("upload_date", "") if isinstance(v, dict) else ""
            conn.execute(
                """INSERT INTO job_videos
                   (job_id, video_id, position, title, channel, duration_sec, view_count, upload_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (job_id, vid_id, pos, v_title, v_channel, v_dur, v_views, v_date),
            )
    return job_id


def update_video_status(job_id: int, video_id: str, status: str, error_msg: str = ""):
    with get_conn() as conn:
        conn.execute(
            "UPDATE job_videos SET status=?, error_msg=? WHERE job_id=? AND video_id=?",
            (status, error_msg, job_id, video_id),
        )
        completed = conn.execute(
            "SELECT COUNT(*) FROM job_videos WHERE job_id=? AND status='completed'", (job_id,)
        ).fetchone()[0]
        failed = conn.execute(
            "SELECT COUNT(*) FROM job_videos WHERE job_id=? AND status='failed'", (job_id,)
        ).fetchone()[0]
        conn.execute(
            "UPDATE jobs SET completed=?, failed=? WHERE id=?",
            (completed, failed, job_id),
        )


def get_pending_videos(job_id: int) -> list[dict]:
    with get_conn() as conn:
        conn.execute(
            "UPDATE job_videos SET status='pending' WHERE job_id=? AND status='processing'",
            (job_id,),
        )
        rows = conn.execute(
            "SELECT * FROM job_videos WHERE job_id=? AND status='pending' ORDER BY position",
            (job_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_job_status(job_id: int) -> Optional[dict]:
    with get_conn() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return None
        videos = conn.execute(
            "SELECT video_id, status, title, error_msg FROM job_videos "
            "WHERE job_id=? ORDER BY position",
            (job_id,),
        ).fetchall()
    return {
        **dict(job),
        "videos": [dict(v) for v in videos],
    }


def _fts_query(raw: str) -> str:
    """Convert plain user text into an FTS5 MATCH expression with prefix wildcards."""
    import re
    # Strip FTS5 special chars except quotes, keep alphanumeric + CJK + Cyrillic
    tokens = re.findall(r'[^\s"\'()|^*]+', raw.strip())
    if not tokens:
        return '""'
    # Each token becomes a prefix query so "elephant" matches "elephants"
    return " ".join(t + "*" for t in tokens)


def search_transcripts(query: str, page: int = 1, per_page: int = 20) -> dict:
    offset = (page - 1) * per_page
    fts_q = _fts_query(query)
    with get_conn() as conn:
        try:
            rows = conn.execute(
                """SELECT t.video_id, t.title, t.channel, t.duration_sec, t.method,
                          t.language, t.upload_date, t.txt_path, t.created_at,
                          snippet(transcripts_fts, 3, '<mark>', '</mark>', '...', 30) AS snippet
                   FROM transcripts_fts
                   JOIN transcripts t ON t.video_id = transcripts_fts.video_id
                   WHERE transcripts_fts MATCH ?
                   ORDER BY rank
                   LIMIT ? OFFSET ?""",
                (fts_q, per_page, offset),
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) FROM transcripts_fts WHERE transcripts_fts MATCH ?", (fts_q,)
            ).fetchone()[0]
        except Exception:
            # Malformed FTS query → return empty
            rows, total = [], 0
    return {"results": [dict(r) for r in rows], "total": total, "page": page}
