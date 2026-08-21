#!/usr/bin/env python3
"""Open the Onegroup workshop template copy screen."""

from __future__ import annotations

import argparse
import webbrowser


TEMPLATE_COPY_URL = "https://docs.google.com/spreadsheets/d/1DzqwIBC4nvX2VFtv6q2imZGHwpUxgDmg1jDvoAu90_s/copy"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print-only", action="store_true", help="Не открывать браузер; только показать ссылку")
    args = parser.parse_args()
    print("Личная таблица для практики:")
    print(TEMPLATE_COPY_URL)
    if args.print_only:
        return 0
    opened = webbrowser.open(TEMPLATE_COPY_URL, new=2)
    if opened:
        print("Открыл страницу Google Sheets. Нажмите «Создать копию», затем скопируйте адрес новой таблицы в Codex.")
    else:
        print("Браузер не открылся автоматически. Откройте ссылку выше вручную.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
