#!/usr/bin/env bash
set -e

if [ ! -d "venv" ]; then
    echo "Виртуальное окружение не найдено. Запусти сначала: ./install.sh"
    exit 1
fi

if [ ! -f ".env" ]; then
    echo ".env не найден. Запусти сначала: ./install.sh"
    exit 1
fi

source venv/bin/activate

# Читаем хост и порт из .env
HOST=$(grep '^HOST=' .env | cut -d= -f2 | tr -d '[:space:]' || echo "127.0.0.1")
PORT=$(grep '^PORT=' .env | cut -d= -f2 | tr -d '[:space:]' || echo "8000")

echo "Запуск YouTube Transcription Tool..."
echo "Открой в браузере: http://${HOST}:${PORT}"
echo "(Ctrl+C для остановки)"
echo ""

python main.py
