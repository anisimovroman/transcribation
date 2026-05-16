"""
Phase 6 tests — core/export.py
Run: venv/bin/python -m pytest tests/test_export.py -v
"""
import zipfile, io

import pytest
import yaml
from pathlib import Path


@pytest.fixture
def vault(tmp_path):
    return str(tmp_path / "vault")


def test_export_to_obsidian_creates_file(vault):
    from core.export import export_to_obsidian
    path = export_to_obsidian(
        video_id="test123",
        title="Test Video Title",
        channel="Test Channel",
        text="Это текст транскрипции.",
        upload_date="20240315",
        duration_sec=600,
        view_count=10000,
        vault_path=vault,
    )
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "Test Video Title" in content
    assert "test123" in content


def test_export_to_obsidian_valid_yaml_frontmatter(vault):
    from core.export import export_to_obsidian
    path = export_to_obsidian(
        video_id="abc123",
        title="My Video",
        channel="My Channel",
        text="Text here.",
        upload_date="20240101",
        duration_sec=300,
        view_count=500,
        vault_path=vault,
    )
    content = path.read_text(encoding="utf-8")
    fm_part = content.split("---")[1]
    parsed = yaml.safe_load(fm_part)
    assert parsed["title"] == "My Video"
    assert parsed["channel"] == "My Channel"
    assert parsed["date"] == "2024-01-01"
    assert isinstance(parsed["tags"], list)
    assert "youtube" in parsed["tags"]


def test_export_to_obsidian_date_format(vault):
    from core.export import export_to_obsidian
    path = export_to_obsidian(
        video_id="vid001", title="T", channel="C", text="t",
        upload_date="20231205", duration_sec=0, view_count=0, vault_path=vault,
    )
    content = path.read_text(encoding="utf-8")
    assert "2023-12-05" in content


def test_export_batch_zip_utf8():
    from core.export import export_batch_zip
    import config
    from unittest.mock import patch
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        td = Path(tmpdir)
        (td / "20240101_Channel_vid001.txt").write_text("Заголовок: Test\n\nТекст", encoding="utf-8")
        (td / "20240102_Channel_vid002.txt").write_text("Заголовок: Test2\n\nТекст2", encoding="utf-8")

        with patch.object(config, "TRANSCRIPTS_DIR", td):
            import importlib, core.export
            importlib.reload(core.export)
            zip_bytes, zip_name = core.export.export_batch_zip(["vid001", "vid002"])

        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        assert len(zf.namelist()) == 2
        for name in zf.namelist():
            content = zf.read(name).decode("utf-8")
            assert "Заголовок:" in content
