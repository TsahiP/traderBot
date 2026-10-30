"""PID lock files: livebot and paperbot trade the same Alpaca paper account, so
only one of them may run at a time, and only one copy of each.

A lock file holds the owner's pid. A lock whose pid is no longer running is
stale and is taken over. On Windows the liveness check uses OpenProcess -
`os.kill(pid, 0)` there does not probe, it terminates the process.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_STILL_ACTIVE = 259


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def holder(path: Path) -> int | None:
    """The live pid holding `path`, or None (missing, unreadable or stale)."""
    try:
        pid = int(Path(path).read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return None
    return pid if pid != os.getpid() and pid_alive(pid) else None


def acquire(path: Path, other: Path | None = None) -> tuple[bool, str]:
    """(ok, reason). Refuses while `path` or `other` is held by a live process."""
    for lock, what in ((other, "the other bot"), (path, "another copy")):
        if lock is None:
            continue
        pid = holder(lock)
        if pid:
            return False, f"{what} is running (pid {pid}, {Path(lock).name})"
    Path(path).write_text(str(os.getpid()), encoding="utf-8")
    return True, "acquired"


def release(path: Path) -> None:
    """Remove `path` if this process holds it."""
    try:
        if int(Path(path).read_text(encoding="utf-8").strip() or 0) == os.getpid():
            Path(path).unlink()
    except (OSError, ValueError):
        pass
