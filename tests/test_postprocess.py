"""
Phase 4 tests — core/postprocess.py
Run: venv/bin/python -m pytest tests/test_postprocess.py -v
"""
import pytest
from core.postprocess import clean_text, postprocess


def test_clean_removes_brackets():
    result = clean_text("привет [музыка] мир")
    assert "[музыка]" not in result
    assert "привет" in result
    assert "мир" in result


def test_clean_removes_parens():
    result = clean_text("hello (смех) world")
    assert "(смех)" not in result
    assert "hello" in result


def test_clean_removes_duplicate_words():
    result = clean_text("это это тест")
    assert result.count("это") == 1


def test_clean_normalizes_spaces():
    result = clean_text("too  many   spaces")
    assert "  " not in result


def test_clean_preserves_length():
    original = "нормальный текст без артефактов"
    result = clean_text(original)
    assert len(result) > 0.9 * len(original)


def test_postprocess_whisper_no_rpunct():
    text = "Привет. [смех] Как дела?"
    result = postprocess(text, method="whisper_medium_cpu", language="ru")
    assert "[смех]" not in result
    assert "Привет" in result


def test_postprocess_youtube_captions_ja_no_rpunct():
    """Японский язык — rpunct не применяется"""
    text = "テスト [音楽] テキスト"
    result = postprocess(text, method="youtube_captions", language="ja")
    assert "[音楽]" not in result


def test_postprocess_returns_nonempty():
    result = postprocess("тест тест", method="youtube_captions", language="ru")
    assert len(result) > 0
