import os
import subprocess
import sys
import time
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent.parent

# A stand-in launcher: starts a child that watches it, prints the child's pid,
# then exits without stopping the child (as a SIGKILL or a closed terminal would).
PARENT = r"""
import os, subprocess, sys
env = dict(os.environ, KA_LAUNCHER_PID=str(os.getpid()))
code = (
    "import time; from app.core.launcher_watch import exit_when_launcher_exits; "
    "assert exit_when_launcher_exits(); print('watching', flush=True); time.sleep(60)"
)
child = subprocess.Popen([sys.executable, "-c", code], env=env, cwd=sys.argv[1], stdout=subprocess.PIPE, text=True, start_new_session=True)
print(child.stdout.readline().strip(), child.pid, flush=True)
"""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # A zombie still answers kill(0); check its state where /proc exists.
    stat = Path(f"/proc/{pid}/stat")
    return not (stat.exists() and stat.read_text().split()[2] == "Z")


def test_child_exits_when_launcher_is_gone():
    parent = subprocess.run([sys.executable, "-c", PARENT, str(SERVICE_DIR)], capture_output=True, text=True, timeout=30)
    state, child_pid = parent.stdout.split()
    child_pid = int(child_pid)
    assert state == "watching", parent.stdout + parent.stderr
    deadline = time.time() + 10
    while time.time() < deadline and _alive(child_pid):
        time.sleep(0.2)
    assert not _alive(child_pid), "child kept running after its launcher exited"


def test_no_watch_without_launcher_pid(monkeypatch):
    monkeypatch.delenv("KA_LAUNCHER_PID", raising=False)
    from app.core.launcher_watch import exit_when_launcher_exits

    assert exit_when_launcher_exits() is False
