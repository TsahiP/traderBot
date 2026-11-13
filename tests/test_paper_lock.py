"""paper.lock: one bot on the account at a time; stale locks are taken over."""
import os
import subprocess
import sys

from paper import lock


def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def test_acquire_and_release(tmp_path):
    mine = tmp_path / "paperbot.lock"
    ok, _ = lock.acquire(mine)
    assert ok and mine.read_text() == str(os.getpid())
    lock.release(mine)
    assert not mine.exists()


def test_a_live_other_bot_refuses(tmp_path):
    other = tmp_path / "livebot.lock"
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        other.write_text(str(child.pid))
        ok, reason = lock.acquire(tmp_path / "paperbot.lock", other=other)
        assert not ok and "other bot" in reason and str(child.pid) in reason
        assert lock.pid_alive(child.pid)
    finally:
        child.kill()
        child.wait()


def test_a_stale_lock_is_taken_over(tmp_path):
    mine = tmp_path / "paperbot.lock"
    dead = _dead_pid()
    mine.write_text(str(dead))
    assert not lock.pid_alive(dead)
    ok, _ = lock.acquire(mine, other=tmp_path / "livebot.lock")
    assert ok and mine.read_text() == str(os.getpid())


def test_release_leaves_someone_elses_lock(tmp_path):
    mine = tmp_path / "paperbot.lock"
    mine.write_text("999999")
    lock.release(mine)
    assert mine.exists()


def test_garbage_in_a_lock_file_is_not_a_holder(tmp_path):
    bad = tmp_path / "x.lock"
    bad.write_text("not a pid")
    assert lock.holder(bad) is None
    assert lock.holder(tmp_path / "missing.lock") is None
