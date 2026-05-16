FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp binary
RUN curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
    -o /usr/local/bin/yt-dlp && chmod a+rx /usr/local/bin/yt-dlp

WORKDIR /app

COPY requirements.txt .
# rpunct pulls PyTorch (~2 GB) — excluded in Docker; code gracefully falls back without it
RUN sed '/^rpunct/d' requirements.txt > /tmp/req.txt && \
    pip install --no-cache-dir -r /tmp/req.txt

COPY . .

# /data is a Railway persistent volume — DB and transcripts live there
ENV HOST=0.0.0.0 \
    TRANSCRIPTS_DIR=/data/transcripts \
    DB_PATH=/data/cache.db \
    WHISPER_MODEL=base \
    WHISPER_DEVICE=cpu \
    MAX_WORKERS=1

RUN mkdir -p /data/transcripts /tmp/transcribation

EXPOSE 8000

CMD ["python", "main.py"]
