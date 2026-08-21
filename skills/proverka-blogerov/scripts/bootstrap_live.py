#!/usr/bin/env python3
"""Prepare an isolated live runtime and run public profile screening."""

from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path


DEPENDENCIES = ("yt-dlp==2026.8.19", "curl_cffi==0.16.1")


def runtime_dir() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / "proverka-blogerov" / "live-runtime"


def runtime_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ready(python: Path, imports: tuple[str, ...] = ("yt_dlp", "curl_cffi")) -> bool:
    if not python.is_file():
        return False
    check = subprocess.run(
        [str(python), "-c", "; ".join(f"import {name}" for name in imports)],
        capture_output=True,
        text=True,
        check=False,
    )
    return check.returncode == 0


def prepare_runtime(
    extra_dependencies: tuple[str, ...] = (),
    extra_imports: tuple[str, ...] = (),
) -> tuple[Path | None, int]:
    root = runtime_dir()
    python = runtime_python(root)
    imports = ("yt_dlp", "curl_cffi", *extra_imports)
    if not ready(python, imports):
        print("Подготавливаю изолированное интернет-окружение навыка…", flush=True)
        root.parent.mkdir(parents=True, exist_ok=True)
        if not python.is_file():
            venv.EnvBuilder(with_pip=True, clear=False).create(root)
        install = subprocess.run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check",
             *DEPENDENCIES, *extra_dependencies],
            check=False,
        )
        if install.returncode:
            print("Не удалось установить зависимости интернет-проверки.", file=sys.stderr)
            return None, install.returncode
    else:
        print("Интернет-окружение уже готово.", flush=True)
    return python, 0


def main() -> int:
    python, code = prepare_runtime()
    if code or python is None:
        return code or 1

    script = Path(__file__).with_name("live_metrics.py")
    result = subprocess.run([str(python), str(script), *sys.argv[1:]], check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
