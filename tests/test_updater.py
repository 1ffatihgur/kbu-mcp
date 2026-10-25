import os
import time

import pytest

from kbu_mcp import config, updater


@pytest.fixture
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "kbu.db")
    monkeypatch.setattr(updater, "LOCK_PATH", tmp_path / "update.lock")
    return tmp_path


def test_missing_index_needs_full_update(tmp_data):
    assert updater.needed_mode() == "full"


def test_lock_is_exclusive_and_released(tmp_data):
    with updater.update_lock():
        assert updater.lock_active()
        with pytest.raises(updater.Busy):
            with updater.update_lock():
                pass
    assert not updater.lock_active()


def test_stale_lock_is_ignored(tmp_data):
    updater.LOCK_PATH.write_text("123")
    old = time.time() - updater.LOCK_STALE_SECONDS - 60
    os.utime(updater.LOCK_PATH, (old, old))
    assert not updater.lock_active()
    with updater.update_lock():  # ölü kilit devralınabilir
        pass


def test_auto_update_can_be_disabled(tmp_data, monkeypatch):
    monkeypatch.setattr(config, "AUTO_UPDATE", False)
    assert updater.maybe_auto_update() is None
