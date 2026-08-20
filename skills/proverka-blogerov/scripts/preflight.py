#!/usr/bin/env python3
"""Check the dependency-free classroom path before running it."""

from pathlib import Path
import json
import platform
import sys


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    required = [root / "assets" / "bloggers.csv", root / "assets" / "evidence.json"]
    missing = [str(path) for path in required if not path.is_file()]
    print(f"Python: {platform.python_version()} ({platform.system()})")
    print("Режим: demo, без внешних зависимостей и сетевых запросов")
    if sys.version_info < (3, 10):
        print("ОШИБКА: нужен Python 3.10 или новее")
        return 1
    if missing:
        print("ОШИБКА: отсутствуют файлы: " + ", ".join(missing))
        return 1
    with required[1].open(encoding="utf-8") as handle:
        evidence = json.load(handle)
    print(f"Готово: найдено {len(evidence)} синтетических кейсов")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
