import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

DESKTOP_DIR = Path(__file__).resolve().parent.parent
FAKE = Path(__file__).resolve().parent / "fake_child.py"
sys.path.insert(0, str(DESKTOP_DIR))

import launcher  # noqa: E402

PID_RE = re.compile(r"FAKE started pid=(\d+)")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait_for(predicate, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def _pids(sup) -> list[int]:
    return [int(m.group(1)) for lines in sup.logs.values() for line in lines for m in [PID_RE.search(line)] if m]


@pytest.fixture
def make_supervisor(tmp_path):
    made = []

    def make(api_mode="ok", worker_mode="ok", env_factory=None, **kwargs):
        port = launcher.free_port(0)
        stops = []
        if env_factory is None:
            def env_factory():
                return {**os.environ, "FAKE_DIR": str(tmp_path), "EMBEDDING_PROVIDER": "ollama"}
        sup = launcher.Supervisor(
            {
                "api": ([sys.executable, str(FAKE), "--port", str(port), "--mode", api_mode], tmp_path),
                "worker": ([sys.executable, str(FAKE), "--mode", worker_mode], tmp_path),
            },
            env_factory,
            f"http://127.0.0.1:{port}/api/health",
            lambda: stops.append(1),
            **kwargs,
        )
        made.append(sup)
        return sup, stops

    yield make
    for sup in made:
        sup.stop()
        for pid in _pids(sup):
            assert _wait_for(lambda: not _alive(pid), 5), f"fake child {pid} still running"


def _run_in_thread(sup):
    result = {}

    def target():
        try:
            sup.start()
            result["rc"] = sup.run(lifeline=False)
        except BaseException as exc:  # surfaced by the test
            result["exc"] = exc

    t = threading.Thread(target=target, daemon=True)
    t.start()
    return t, result


def test_restart_code_restarts_children_with_fresh_env(make_supervisor, tmp_path):
    providers = iter(["ollama", "voyage", "voyage", "voyage"])

    def env_factory():
        return {**os.environ, "FAKE_DIR": str(tmp_path), "EMBEDDING_PROVIDER": next(providers)}

    sup, stops = make_supervisor(api_mode="exit75-once", env_factory=env_factory)
    t, result = _run_in_thread(sup)

    def restarted():
        return [line for line in sup.logs["api"] if "FAKE started" in line]

    assert _wait_for(lambda: len(restarted()) >= 2, 20), list(sup.logs["api"])
    started = restarted()
    assert len(started) == 2
    assert "provider=ollama" in started[0]
    assert "provider=voyage" in started[1]
    # the worker was restarted too, with the fresh env
    worker_started = lambda: [line for line in sup.logs["worker"] if "FAKE started" in line]  # noqa: E731
    assert _wait_for(lambda: len(worker_started()) == 2, 10), list(sup.logs["worker"])
    assert "provider=voyage" in worker_started()[1]
    assert stops == []
    assert t.is_alive() and "exc" not in result

    sup.request_stop()
    t.join(15)
    assert result.get("rc") == 0
    assert stops == [1]


def test_child_crash_stops_everything(make_supervisor):
    sup, stops = make_supervisor(worker_mode="crash")
    t, result = _run_in_thread(sup)
    t.join(20)
    assert not t.is_alive()
    assert "exc" not in result, result.get("exc")
    assert result["rc"] == 1
    assert stops == [1]
    for pid in _pids(sup):
        assert not _alive(pid)


def test_startup_failure_exits_nonzero(make_supervisor):
    sup, stops = make_supervisor(api_mode="never-healthy", startup_timeout=3)
    with pytest.raises(RuntimeError) as info:
        sup.start()
    assert "FAKE started" in str(info.value)


def test_lifeline_eof_stops_cleanly(tmp_path):
    home = tmp_path / "home"
    env = {
        **os.environ,
        "KA_FAKE_CHILDREN": "1",
        "KNOWLEDGE_AGENT_HOME": str(home),
        "FAKE_DIR": str(tmp_path),
        "FAKE_MODE": "ok",
        "PYTHONUNBUFFERED": "1",
    }
    proc = subprocess.Popen(
        [sys.executable, str(DESKTOP_DIR / "launcher.py"), "--lifeline", "--no-browser", "--port", "0"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    lines: list[str] = []
    reader = threading.Thread(target=lambda: lines.extend(iter(proc.stdout.readline, "")), daemon=True)
    reader.start()
    try:
        assert _wait_for(lambda: any(line.startswith("Ready:") for line in lines), 20), "".join(lines)
        pids = [int(m.group(1)) for line in lines for m in [PID_RE.search(line)] if m]
        assert len(pids) == 2, "".join(lines)
        assert (home / "running.json").exists()

        proc.stdin.close()
        assert proc.wait(timeout=15) == 0, "".join(lines)
        for pid in pids:
            assert not _alive(pid), f"fake child {pid} still running"
        assert not (home / "running.json").exists()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
