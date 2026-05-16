#!/usr/bin/env bash
set -e

# ─────────────────────────────────────────────
#  YouTube Transcription Tool — установщик
# ─────────────────────────────────────────────

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}!${NC} $1"; }
err()  { echo -e "${RED}✗ $1${NC}"; exit 1; }
info() { echo -e "${BLUE}→${NC} $1"; }

echo ""
echo -e "${BOLD}YouTube Transcription Tool — установка${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── 1. Python ────────────────────────────────
info "Проверка Python..."
PYTHON=""
for cmd in python3.13 python3.12 python3.11 python3; do
    if command -v "$cmd" &>/dev/null; then
        VER=$("$cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        MAJOR=$(echo "$VER" | cut -d. -f1)
        MINOR=$(echo "$VER" | cut -d. -f2)
        if [ "$MAJOR" -eq 3 ] && [ "$MINOR" -ge 11 ]; then
            PYTHON="$cmd"
            ok "Python $VER найден ($cmd)"
            break
        fi
    fi
done
[ -z "$PYTHON" ] && err "Нужен Python 3.11+. Установи с https://www.python.org/downloads/"

# ── 2. ffmpeg ────────────────────────────────
info "Проверка ffmpeg..."
if command -v ffmpeg &>/dev/null; then
    ok "ffmpeg найден"
else
    warn "ffmpeg не найден — пробую установить..."
    if command -v brew &>/dev/null; then
        brew install ffmpeg
        ok "ffmpeg установлен"
    elif command -v apt-get &>/dev/null; then
        sudo apt-get install -y ffmpeg
        ok "ffmpeg установлен"
    else
        err "Установи ffmpeg вручную: https://ffmpeg.org/download.html"
    fi
fi

# ── 3. yt-dlp ───────────────────────────────
info "Проверка yt-dlp..."
if command -v yt-dlp &>/dev/null; then
    ok "yt-dlp найден"
else
    warn "yt-dlp не найден — устанавливаю..."
    if command -v brew &>/dev/null; then
        brew install yt-dlp
    else
        "$PYTHON" -m pip install --user yt-dlp
    fi
    ok "yt-dlp установлен"
fi

# ── 4. Виртуальное окружение ─────────────────
info "Создание виртуального окружения..."
if [ ! -d "venv" ]; then
    "$PYTHON" -m venv venv
    ok "venv создан"
else
    ok "venv уже существует"
fi

source venv/bin/activate

# ── 5. Зависимости ───────────────────────────
info "Установка зависимостей Python..."
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
ok "Основные зависимости установлены"

# Apple Silicon: mlx-whisper (GPU-ускорение)
ARCH=$(uname -m)
OS=$(uname -s)
if [ "$OS" = "Darwin" ] && [ "$ARCH" = "arm64" ]; then
    info "Обнаружен Apple Silicon — устанавливаю MLX Whisper (GPU)..."
    pip install mlx-whisper --quiet
    ok "mlx-whisper установлен (будет использовать GPU M-серии)"
fi

# ── 6. Конфиг .env ───────────────────────────
if [ ! -f ".env" ]; then
    echo ""
    echo -e "${BOLD}Настройка .env${NC}"
    echo "──────────────────────────────────────"
    echo "Нужен ключ YouTube Data API v3."
    echo "Получить: https://console.cloud.google.com"
    echo "  1. Создай проект"
    echo "  2. APIs & Services → Enable APIs → YouTube Data API v3"
    echo "  3. Credentials → Create API Key"
    echo ""
    read -p "Вставь YouTube API Key: " YT_KEY
    [ -z "$YT_KEY" ] && err "API Key обязателен"

    cp .env.example .env
    if [ "$OS" = "Darwin" ]; then
        sed -i '' "s|your_key_here|${YT_KEY}|" .env
    else
        sed -i "s|your_key_here|${YT_KEY}|" .env
    fi

    # На Apple Silicon — medium через MLX быстрый, оставляем как есть
    # На обычном CPU — рекомендуем small
    if [ "$OS" != "Darwin" ] || [ "$ARCH" != "arm64" ]; then
        warn "Не Apple Silicon: ставлю WHISPER_MODEL=small (быстрее на CPU)"
        if [ "$OS" = "Darwin" ]; then
            sed -i '' "s|WHISPER_MODEL=medium|WHISPER_MODEL=small|" .env
        else
            sed -i "s|WHISPER_MODEL=medium|WHISPER_MODEL=small|" .env
        fi
    fi

    ok ".env создан"
else
    ok ".env уже существует"
fi

# ── 7. Директории ────────────────────────────
mkdir -p transcripts data
ok "Директории transcripts/ и data/ готовы"

# ── 8. Итог ──────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GREEN}${BOLD}✓ Установка завершена!${NC}"
echo ""
echo "Запуск:"
echo -e "  ${BOLD}./start.sh${NC}"
echo ""
echo "Или вручную:"
echo -e "  ${BOLD}source venv/bin/activate && python main.py${NC}"
echo ""
