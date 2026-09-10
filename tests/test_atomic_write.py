import os

import pytest

from scripts.utils.atomic_write import atomic_write_text


def test_writes_full_content(tmp_path):
    path = tmp_path / "file.txt"
    atomic_write_text(path, "hello world")
    assert path.read_text(encoding="utf-8") == "hello world"


def test_creates_nested_parent_directories(tmp_path):
    path = tmp_path / "a" / "b" / "c" / "file.txt"
    atomic_write_text(path, "content")
    assert path.read_text(encoding="utf-8") == "content"


def test_overwrites_existing_file_completely(tmp_path):
    path = tmp_path / "file.txt"
    path.write_text("old content that is much longer than the new one", encoding="utf-8")
    atomic_write_text(path, "new")
    assert path.read_text(encoding="utf-8") == "new"


def test_no_temp_file_left_behind_after_success(tmp_path):
    path = tmp_path / "file.txt"
    atomic_write_text(path, "content")
    leftovers = [p for p in tmp_path.iterdir() if p != path]
    assert leftovers == []


def test_non_ascii_content_round_trips(tmp_path):
    path = tmp_path / "file.txt"
    atomic_write_text(path, "Лучшие видеокарты 2026")
    assert path.read_text(encoding="utf-8") == "Лучшие видеокарты 2026"


def test_interrupted_write_leaves_original_file_untouched(tmp_path, monkeypatch):
    """Simulates a crash between finishing the temp file and the atomic
    replace -- the original file must survive completely intact, and the
    failure must propagate rather than being swallowed."""
    path = tmp_path / "file.txt"
    atomic_write_text(path, "original content")

    def _boom(src, dst):
        raise OSError("simulated crash during replace")

    monkeypatch.setattr(os, "replace", _boom)

    with pytest.raises(OSError):
        atomic_write_text(path, "new content that should never land")

    assert path.read_text(encoding="utf-8") == "original content"


def test_interrupted_write_does_not_leak_temp_files(tmp_path, monkeypatch):
    path = tmp_path / "file.txt"
    atomic_write_text(path, "original content")

    def _boom(src, dst):
        raise OSError("simulated crash during replace")

    monkeypatch.setattr(os, "replace", _boom)

    with pytest.raises(OSError):
        atomic_write_text(path, "new content")

    remaining = list(tmp_path.iterdir())
    assert remaining == [path]


def test_failure_while_writing_temp_file_does_not_touch_destination(tmp_path, monkeypatch):
    path = tmp_path / "file.txt"
    atomic_write_text(path, "original content")

    real_fdopen = os.fdopen

    def _failing_fdopen(fd, *args, **kwargs):
        handle = real_fdopen(fd, *args, **kwargs)
        handle.write("partial")
        handle.close()  # release the handle before raising -- Windows can't unlink an open file
        raise OSError("simulated disk-full mid-write")

    monkeypatch.setattr(os, "fdopen", _failing_fdopen)

    with pytest.raises(OSError):
        atomic_write_text(path, "new content")

    assert path.read_text(encoding="utf-8") == "original content"
    remaining = list(tmp_path.iterdir())
    assert remaining == [path]
