import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_backend as bb  # noqa: E402


def test_detect_target():
    assert bb.detect_target("Darwin", "arm64") == "aarch64-apple-darwin"
    assert bb.detect_target("Windows", "AMD64") == "x86_64-pc-windows-msvc"
    assert bb.detect_target("Linux", "x86_64") == "x86_64-unknown-linux-gnu"
    with pytest.raises(SystemExit):
        bb.detect_target("Darwin", "x86_64")


def test_layout_per_platform(tmp_path):
    assert bb.python_exe(tmp_path, "x86_64-pc-windows-msvc") == tmp_path / "python" / "python.exe"
    assert bb.python_exe(tmp_path, "aarch64-apple-darwin") == tmp_path / "python" / "bin" / "python3"
    assert bb.site_packages(tmp_path, "x86_64-pc-windows-msvc") == tmp_path / "python" / "Lib" / "site-packages"


def test_pinned_builds_have_sha256():
    assert set(bb.PYTHON_BUILDS) == {"aarch64-apple-darwin", "x86_64-pc-windows-msvc", "x86_64-unknown-linux-gnu"}
    for url, sha in bb.PYTHON_BUILDS.values():
        assert "cpython-3.12." in url and len(sha) == 64


def test_copy_excludes(tmp_path):
    src = tmp_path / "src"
    for rel in ("app/main.py", "app/__pycache__/x.pyc", "tests/t.py", ".env", ".env.local", "Dockerfile", "requirements.txt", "app/a.pyc"):
        f = src / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    import shutil

    dst = tmp_path / "dst"
    shutil.copytree(src, dst, ignore=bb.ignore_copy)
    kept = sorted(str(p.relative_to(dst)) for p in dst.rglob("*") if p.is_file())
    assert kept == ["app/main.py", "requirements.txt"]
