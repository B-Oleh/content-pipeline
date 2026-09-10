import os

import pytest

from scripts.research.state.store import JsonListStore


def test_load_returns_empty_list_when_file_missing(tmp_path):
    store = JsonListStore(tmp_path / "missing.json")
    assert store.load() == []


def test_save_creates_nested_parent_directories(tmp_path):
    # Deliberately nested and composed via pathlib's "/" operator so this
    # exercises directory creation identically regardless of the platform's
    # native path separator (POSIX "/" vs Windows "\\").
    path = tmp_path / "a" / "b" / "c" / "state.json"
    store = JsonListStore(path)

    store.save([{"x": 1}])

    assert path.exists()
    assert store.load() == [{"x": 1}]


def test_append_adds_one_item(tmp_path):
    store = JsonListStore(tmp_path / "state.json")
    store.append({"a": 1})
    store.append({"a": 2})
    assert store.load() == [{"a": 1}, {"a": 2}]


def test_extend_adds_multiple_items(tmp_path):
    store = JsonListStore(tmp_path / "state.json")
    store.extend([{"a": 1}, {"a": 2}])
    store.extend([{"a": 3}])
    assert store.load() == [{"a": 1}, {"a": 2}, {"a": 3}]


def test_extend_with_empty_list_is_a_no_op_and_does_not_create_file(tmp_path):
    path = tmp_path / "state.json"
    store = JsonListStore(path)
    store.extend([])
    assert not path.exists()


def test_save_overwrites_previous_contents(tmp_path):
    store = JsonListStore(tmp_path / "state.json")
    store.save([{"a": 1}])
    store.save([{"b": 2}])
    assert store.load() == [{"b": 2}]


def test_non_ascii_content_round_trips(tmp_path):
    store = JsonListStore(tmp_path / "state.json")
    store.save([{"title": "Лучшие видеокарты"}])
    assert store.load() == [{"title": "Лучшие видеокарты"}]


def test_interrupted_save_does_not_corrupt_existing_history(tmp_path, monkeypatch):
    """An interrupted write (e.g. process killed mid-save) must never leave
    game_history.json truncated -- that would crash every subsequent run's
    load_game_history() with json.JSONDecodeError. save() goes through
    atomic_write_text, so the pre-existing valid content must survive."""
    store = JsonListStore(tmp_path / "state.json")
    store.save([{"game_title": "Hades II"}])

    def _boom(src, dst):
        raise OSError("simulated crash")

    monkeypatch.setattr(os, "replace", _boom)

    with pytest.raises(OSError):
        store.save([{"game_title": "Hades II"}, {"game_title": "Cyberpunk 2077"}])

    # The store must still load its last good state, not raise JSONDecodeError.
    assert store.load() == [{"game_title": "Hades II"}]
