"""Exit when the desktop launcher that started this process goes away.

The launcher normally stops its children itself. But if it is killed outright
(SIGKILL, a crash, the desktop shell's last-resort kill), children started in
their own session would keep running and keep serving the app with nothing
supervising them. The launcher passes its pid as KA_LAUNCHER_PID; a daemon
thread here exits this process as soon as that pid is no longer our parent.
Server mode never sets the variable, so this is a no-op there.
"""

from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

POLL_SECONDS = 1.0


def _launcher_gone(launcher_pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, launcher_pid)
        if not handle:
            return True
        try:
            WAIT_TIMEOUT = 0x102
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) != WAIT_TIMEOUT
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    # POSIX: an orphan is re-parented (to init/launchd), so the parent changes.
    return os.getppid() != launcher_pid


def _watch(launcher_pid: int) -> None:
    while True:
        time.sleep(POLL_SECONDS)
        if _launcher_gone(launcher_pid):
            logger.warning("desktop launcher (pid %s) is gone; exiting", launcher_pid)
            os._exit(0)


def exit_when_launcher_exits() -> bool:
    """Start the watcher if KA_LAUNCHER_PID is set. Returns whether it started."""
    raw = os.environ.get("KA_LAUNCHER_PID")
    if not raw:
        return False
    try:
        launcher_pid = int(raw)
    except ValueError:
        return False
    if _launcher_gone(launcher_pid):
        os._exit(0)
    threading.Thread(target=_watch, args=(launcher_pid,), name="launcher-watch", daemon=True).start()
    return True
