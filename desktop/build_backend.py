"""Assemble the portable backend the Tauri app ships as a resource.

    python desktop/build_backend.py [--target TRIPLE] [--skip-ui]

Output: desktop/tauri/src-tauri/resources/backend/ with
    python/            python-build-standalone CPython 3.12 + all dependencies
    api/ worker/ sql/  service source and schema
    ui/                static export of services/frontend (unless --skip-ui)
    launcher.py desktop_config.py BUILD_INFO.json

The bundled interpreter has to be able to run, so only the host's own target
can be built (CI builds each platform on its own runner).
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

DESKTOP = Path(__file__).resolve().parent
REPO = DESKTOP.parent
CACHE = DESKTOP / ".cache"
OUT = DESKTOP / "tauri" / "src-tauri" / "resources" / "backend"

_PBS = "https://github.com/astral-sh/python-build-standalone/releases/download/20261013"
PYTHON_VERSION = "3.12.15"

# target triple -> (url, sha256 from the release's SHA256SUMS)
PYTHON_BUILDS: dict[str, tuple[str, str]] = {
    "aarch64-apple-darwin": (
        f"{_PBS}/cpython-{PYTHON_VERSION}%2B20261013-aarch64-apple-darwin-install_only_stripped.tar.gz",
        "9866e11cf2ae9caa1e671383786f248859a52dd8bfb702fb4c0dc2c65f1d36de",
    ),
    "x86_64-pc-windows-msvc": (
        f"{_PBS}/cpython-{PYTHON_VERSION}%2B20261013-x86_64-pc-windows-msvc-install_only_stripped.tar.gz",
        "7a2aef48e5ee569e310b29b32681debbc5d888e92790d85331a47736c6b06aad",
    ),
    "x86_64-unknown-linux-gnu": (
        f"{_PBS}/cpython-{PYTHON_VERSION}%2B20261013-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz",
        "178d3d83b6e8e99285a65d3fce9c16b87952cc5efdb4d1a0e3ff3be18ff0fc9c",
    ),
}

# Names (fnmatch) skipped when copying the api/worker source.
COPY_EXCLUDES = ("__pycache__", "tests", ".env*", "*.pyc", "Dockerfile")


def detect_target(system: str | None = None, machine: str | None = None) -> str:
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    if system == "darwin" and machine in ("arm64", "aarch64"):
        return "aarch64-apple-darwin"
    if system == "windows" and machine in ("amd64", "x86_64"):
        return "x86_64-pc-windows-msvc"
    if system == "linux" and machine in ("x86_64", "amd64"):
        return "x86_64-unknown-linux-gnu"
    raise SystemExit(f"unsupported platform {system}/{machine}; supported: {', '.join(PYTHON_BUILDS)}")


def is_windows(target: str) -> bool:
    return "windows" in target


def python_exe(backend: Path, target: str) -> Path:
    py = backend / "python"
    return py / "python.exe" if is_windows(target) else py / "bin" / "python3"


def site_packages(backend: Path, target: str) -> Path:
    py = backend / "python"
    if is_windows(target):
        return py / "Lib" / "site-packages"
    return py / "lib" / "python3.12" / "site-packages"


def ignore_copy(_dir: str, names: list[str]) -> set[str]:
    return {n for n in names if any(fnmatch.fnmatch(n, pat) for pat in COPY_EXCLUDES)}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch_python(target: str) -> Path:
    url, expected = PYTHON_BUILDS[target]
    CACHE.mkdir(parents=True, exist_ok=True)
    archive = CACHE / url.rsplit("/", 1)[1].replace("%2B", "+")
    if not archive.exists() or sha256_of(archive) != expected:
        print(f"Downloading {url}")
        tmp = archive.with_suffix(".part")
        with urllib.request.urlopen(url) as r, tmp.open("wb") as f:
            shutil.copyfileobj(r, f)
        tmp.replace(archive)
    actual = sha256_of(archive)
    if actual != expected:
        archive.unlink(missing_ok=True)
        raise SystemExit(f"checksum mismatch for {archive.name}: expected {expected}, got {actual}")
    return archive


def extract_python(archive: Path, backend: Path) -> None:
    with tempfile.TemporaryDirectory(dir=backend.parent) as tmp:
        with tarfile.open(archive) as tar:
            if hasattr(tarfile, "data_filter"):
                tar.extractall(tmp, filter="data")
            else:  # pragma: no cover - old host pythons
                tar.extractall(tmp)
        shutil.move(str(Path(tmp) / "python"), str(backend / "python"))


def install_dependencies(backend: Path, target: str) -> None:
    py = python_exe(backend, target)
    # requirements.txt has a relative `-r ../services/...`; run from desktop/.
    # Isolated (-I) and a scrubbed env: the builder's user site, PYTHONPATH or
    # PYTHONHOME must not satisfy requirements and leave them out of the bundle.
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    env["PYTHONNOUSERSITE"] = "1"
    subprocess.run(
        [str(py), "-I", "-m", "pip", "install", "--no-compile", "--disable-pip-version-check", "-r", "requirements.txt"],
        cwd=DESKTOP,
        check=True,
        env=env,
    )


CHECK_IMPORTS = ("fastapi", "uvicorn", "pgserver", "asyncpg", "fakeredis", "sqlalchemy", "pgvector", "platformdirs", "pkg_resources")


def check_imports(backend: Path, target: str) -> None:
    """Fail the build if the bundled interpreter, run isolated, can't import
    the key packages."""
    py = python_exe(backend, target)
    code = "import importlib,sys\nfor m in sys.argv[1:]: importlib.import_module(m)"
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    r = subprocess.run([str(py), "-I", "-c", code, *CHECK_IMPORTS], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise SystemExit(f"bundle sanity check failed: a required package is missing from the bundle\n{r.stderr}")
    print("Bundle import check passed")


def copy_sources(backend: Path) -> None:
    for name in ("api", "worker"):
        shutil.copytree(REPO / "services" / name, backend / name, ignore=ignore_copy)
    (backend / "sql").mkdir()
    for sql in sorted((REPO / "infra" / "postgres").glob("*.sql")):
        shutil.copy2(sql, backend / "sql" / sql.name)
    for name in ("launcher.py", "desktop_config.py"):
        shutil.copy2(DESKTOP / name, backend / name)


def build_ui(backend: Path) -> None:
    frontend = REPO / "services" / "frontend"
    npm = "npm.cmd" if os.name == "nt" else "npm"
    env = {**os.environ, "DESKTOP_BUILD": "1"}
    if not (frontend / "node_modules").is_dir():
        subprocess.run([npm, "ci"], cwd=frontend, check=True, env=env)
    subprocess.run([npm, "run", "build"], cwd=frontend, check=True, env=env)
    out = frontend / "out"
    if not out.is_dir():
        raise SystemExit(f"npm run build produced no {out}; is DESKTOP_BUILD=1 export configured?")
    shutil.copytree(out, backend / "ui")


def clean(backend: Path, target: str) -> None:
    """Strip what the runtime never imports. setuptools stays: the API
    requirements pin it and opentelemetry-instrumentation imports
    pkg_resources at startup."""
    sp = site_packages(backend, target)
    py = backend / "python"
    shutil.rmtree(sp / "pip", ignore_errors=True)
    for p in sp.glob("pip-*"):
        shutil.rmtree(p, ignore_errors=True)
    scripts = py / "Scripts" if is_windows(target) else py / "bin"
    for p in scripts.glob("pip*"):
        p.unlink()
    stdlib = py / "Lib" if is_windows(target) else py / "lib" / "python3.12"
    for name in ("ensurepip", "test", "idlelib", "turtledemo"):
        shutil.rmtree(stdlib / name, ignore_errors=True)
    # Package test suites and bytecode caches, anywhere in the bundle's python.
    for dirpath, dirnames, _files in os.walk(py):
        for d in list(dirnames):
            if d == "__pycache__" or (d in ("tests", "test") and Path(dirpath).is_relative_to(sp)):
                shutil.rmtree(Path(dirpath) / d, ignore_errors=True)
                dirnames.remove(d)


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", choices=sorted(PYTHON_BUILDS), help="default: this machine")
    parser.add_argument("--skip-ui", action="store_true", help="don't run the frontend build (ui/ is left absent)")
    args = parser.parse_args()
    target = args.target or detect_target()
    if target != detect_target():
        raise SystemExit(f"cannot build {target} on this machine: the bundled interpreter must run here to install packages")

    archive = fetch_python(target)
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    extract_python(archive, OUT)
    install_dependencies(OUT, target)
    copy_sources(OUT)
    if not args.skip_ui:
        build_ui(OUT)
    clean(OUT, target)
    check_imports(OUT, target)
    (OUT / "BUILD_INFO.json").write_text(
        json.dumps({"target": target, "python_version": PYTHON_VERSION, "git_sha": git_sha()}, indent=2) + "\n"
    )
    print(f"Backend bundle ready: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
