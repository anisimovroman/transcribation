# YouTube Transcription Tool

Локальный инструмент для массовой транскрибации YouTube видео. Скачивает субтитры или транскрибирует аудио через Whisper — всё работает на твоём компьютере.

## Возможности

- Транскрибация целых каналов или по поисковому запросу
- Приоритет YouTube субтитров (мгновенно) → fallback на Whisper (локально)
- Apple Silicon (M1–M4): Whisper работает на GPU через MLX — в 10× быстрее CPU
- Полнотекстовый поиск по всем транскрипциям
- Экспорт в Obsidian (Markdown с YAML frontmatter)
- Оценка времени до запуска транскрибации

## Требования

| Инструмент | Версия | Установка |
|---|---|---|
| Python | 3.11+ | [python.org](https://www.python.org/downloads/) |
| ffmpeg | любая | `brew install ffmpeg` / [ffmpeg.org](https://ffmpeg.org/download.html) |
| yt-dlp | любая | `brew install yt-dlp` |
| YouTube API Key | — | [инструкция ниже](#youtube-api-key) |

## Установка

```bash
git clone https://github.com/YOUR_USERNAME/transcribation.git
cd transcribation
chmod +x install.sh start.sh
./install.sh
```

Скрипт сам:
- Проверит Python, ffmpeg, yt-dlp (установит если нет brew)
- Создаст виртуальное окружение и установит зависимости
- На Apple Silicon установит MLX Whisper (GPU-ускорение)
- Попросит YouTube API Key и создаст `.env`

## Запуск

```bash
./start.sh
```

Открой в браузере: **http://127.0.0.1:8000**

## YouTube API Key

1. Зайди на [console.cloud.google.com](https://console.cloud.google.com)
2. Создай проект (или выбери существующий)
3. **APIs & Services → Enable APIs → YouTube Data API v3** → Enable
4. **APIs & Services → Credentials → Create Credentials → API Key**
5. Скопируй ключ — вставишь при установке

Бесплатная квота: **10 000 запросов в день** (~100–200 поисков или загрузок каналов).

## Настройки (.env)

После установки `.env` лежит в корне проекта:

```env
WHISPER_MODEL=medium     # tiny | base | small | medium | large-v3-turbo
MAX_WORKERS=2            # параллельных транскрипций
MAX_VIDEO_DURATION_SEC=10800  # максимальная длина видео (3 часа)
PORT=8000                # порт сервера
```

### Выбор модели Whisper

| Модель | CPU | Apple M-серия | Качество |
|---|---|---|---|
| `tiny` | очень быстро | мгновенно | низкое |
| `base` | быстро | очень быстро | среднее |
| `small` | средне | быстро | хорошее |
| `medium` | медленно | **быстро** | отличное ✓ |
| `large-v3-turbo` | очень медленно | быстро | лучшее |

На Apple Silicon — `medium` или `large-v3-turbo`. На CPU — `small` или `base`.

## Структура проекта

```
transcribation/
├── install.sh          # установщик
├── start.sh            # запуск сервера
├── main.py             # точка входа FastAPI
├── config.py           # конфигурация из .env
├── requirements.txt    # зависимости
├── .env.example        # шаблон конфига
├── api/routes.py       # HTTP эндпоинты
├── core/
│   ├── transcriber.py  # Whisper + YouTube субтитры
│   ├── storage.py      # SQLite + FTS5 поиск
│   ├── youtube.py      # YouTube Data API
│   ├── export.py       # Obsidian / ZIP экспорт
│   └── postprocess.py  # постобработка текста
├── static/             # CSS, JS
├── templates/          # HTML
├── transcripts/        # сохранённые .txt (создаётся автоматически)
└── data/               # SQLite база (создаётся автоматически)
```

## Частые проблемы

**YouTube блокирует запрос субтитров**  
Это нормально — инструмент автоматически переключается на Whisper.

**yt-dlp не скачивает аудио**  
Обнови: `brew upgrade yt-dlp` или активируй venv и `pip install -U yt-dlp`

**Медленная транскрибация на CPU**  
Поставь модель поменьше: `WHISPER_MODEL=small` в `.env`

**Квота YouTube исчерпана (429)**  
Лимит 10 000 запросов/день. Жди следующего дня или создай второй API Key.
