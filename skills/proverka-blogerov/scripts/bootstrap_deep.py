#!/usr/bin/env python3
"""Install the isolated runtime and deeply screen every blogger in a public sheet."""

from __future__ import annotations

import argparse
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

from bootstrap_live import prepare_runtime


HERE = Path(__file__).resolve().parent
DEFAULT_BRIEF = HERE.parent / "assets" / "onegroup-brief.json"


def sheet_id(value: str) -> str:
    match = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", value)
    candidate = match.group(1) if match else value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,}", candidate):
        raise ValueError("не удалось определить ID Google-таблицы")
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Вся таблица × 10 роликов: метрики, речь, мат, конкуренты и план записи H/N:S."
    )
    parser.add_argument("--sheet-url", required=True, help="ссылка на личную публичную копию Google-таблицы")
    parser.add_argument("--videos", type=int, default=10)
    parser.add_argument("--max-profiles", type=int, default=0,
                        help="0 — все профили; N — первые N только для диагностического теста")
    parser.add_argument("--brief", type=Path, default=DEFAULT_BRIEF)
    parser.add_argument("--output", type=Path, default=HERE.parent / "output" / "onegroup-full")
    args = parser.parse_args()

    if not 1 <= args.videos <= 20:
        parser.error("videos должен быть в диапазоне 1–20")
    if args.max_profiles < 0:
        parser.error("max-profiles не может быть отрицательным")
    if not args.brief.is_file():
        parser.error(f"не найден бриф: {args.brief}")
    try:
        spreadsheet_id = sheet_id(args.sheet_url)
    except ValueError as exc:
        parser.error(str(exc))

    apple_silicon = platform.system() == "Darwin" and platform.machine() == "arm64"
    if apple_silicon:
        speech_dependencies = ("mlx-whisper==0.4.3", "imageio-ffmpeg==0.6.0")
        speech_imports = ("mlx_whisper", "imageio_ffmpeg")
    else:
        speech_dependencies = ("faster-whisper==1.2.1", "imageio-ffmpeg==0.6.0")
        speech_imports = ("faster_whisper", "imageio_ffmpeg")
    python, code = prepare_runtime(speech_dependencies, speech_imports)
    if code or python is None:
        return code or 1

    args.output.mkdir(parents=True, exist_ok=True)
    runtime_bin = python.parent
    ytdlp = runtime_bin / ("yt-dlp.exe" if os.name == "nt" else "yt-dlp")
    env = os.environ.copy()
    env["PATH"] = str(runtime_bin) + os.pathsep + env.get("PATH", "")
    env["YTDLP_BIN"] = str(ytdlp)
    env["PYTHONUTF8"] = "1"
    ffmpeg_result = subprocess.run(
        [str(python), "-c", "from imageio_ffmpeg import get_ffmpeg_exe; print(get_ffmpeg_exe())"],
        capture_output=True, text=True, check=False,
    )
    if ffmpeg_result.returncode or not ffmpeg_result.stdout.strip():
        print("Не удалось подготовить ffmpeg для локального распознавания речи.", file=sys.stderr)
        return ffmpeg_result.returncode or 1
    env["FFMPEG_BIN"] = ffmpeg_result.stdout.strip()

    command = [
        str(python), str(HERE / "sheet_run.py"),
        "--sheet", spreadsheet_id,
        "--videos", str(args.videos),
        "--max-profiles", str(args.max_profiles),
        "--brief", str(args.brief),
        "--workdir", str(args.output),
    ]
    print("Запускаю глубокую проверку. Промежуточный результат сохраняется после каждого блогера.", flush=True)
    result = subprocess.run(command, env=env, check=False)
    if result.returncode:
        print(f"Проверка остановилась с кодом {result.returncode}. Уже готовые строки сохранены в {args.output}",
              file=sys.stderr)
        return result.returncode

    build = subprocess.run([
        str(python), str(HERE / "make_paste.py"),
        "--input", str(args.output / "результат.json"),
        "--output", str(args.output),
    ], env=env, check=False)
    if build.returncode:
        return build.returncode

    print("\nГлубокий проход завершён.")
    print(f"План записи: {args.output / 'sheet-write-plan.json'}")
    print("Перед записью покажи пользователю диапазоны и получи подтверждение.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
