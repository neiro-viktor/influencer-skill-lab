#!/usr/bin/env python3
"""
Самопроверка навыка и пополнение словаря.

Три режима:

  python3 verify.py
      Прогон контрольного набора. Показывает ложные срабатывания и пропуски.
      Если что-то не сошлось — код возврата 1. Запускать перед каждым разбором
      подборки и после любой правки словаря.

  python3 verify.py --ложное мандарин --где "синтетический пример"
      Менеджер увидел в отчёте ошибку. Слово уходит в исключения И СРАЗУ в
      контрольный набор — чтобы эта ошибка больше никогда не вернулась.

  python3 verify.py --мат ебанулся --где "@nickname"
      Обратный случай: мат прошёл мимо. Слово идёт в контрольный набор,
      а если ни один корень его не ловит — печатается подсказка, какой добавить.

Смысл: словарь живёт не в голове и не в коде, а в файле, который команда
пополняет сама, и каждое пополнение проверяется тестом.
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
SLOVAR = HERE.parent / "slovar.json"


def load():
    return json.loads(SLOVAR.read_text(encoding="utf-8"))


def save(d):
    d["обновлён"] = date.today().isoformat()
    SLOVAR.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def check(d):
    """Прогон контрольного набора. Возвращает (ложные, пропущенные)."""
    sys.path.insert(0, str(HERE))
    import importlib
    import check_roller
    importlib.reload(check_roller)

    ложные = [w for w in d["контрольный_набор"]["не_мат"]
              if check_roller.find_profanity([("00:00", w)])]
    пропущенные = [w for w in d["контрольный_набор"]["мат"]
                   if not check_roller.find_profanity([("00:00", w)])]
    return ложные, пропущенные


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ложное", help="слово, ошибочно помеченное как мат")
    ap.add_argument("--мат", help="мат, который навык пропустил")
    ap.add_argument("--где", default="не указано", help="у какого блогера нашли")
    args = ap.parse_args()

    d = load()

    if args.ложное:
        w = args.ложное.lower().strip()
        if not any(e["слово"] == w for e in d["исключения"]):
            d["исключения"].append({"слово": w, "почему": "отмечено менеджером как ложное",
                                    "нашли": args.где, "дата": date.today().isoformat()})
        if w not in d["контрольный_набор"]["не_мат"]:
            d["контрольный_набор"]["не_мат"].append(w)
        save(d)
        print(f"«{w}» добавлено в исключения и в контрольный набор ({args.где}).")

    if args.мат:
        w = args.мат.lower().strip()
        if w not in d["контрольный_набор"]["мат"]:
            d["контрольный_набор"]["мат"].append(w)
        save(d)
        print(f"«{w}» добавлено в контрольный набор ({args.где}).")

    ложные, пропущенные = check(d)

    print(f"\nСловарь: {len(d['мат_корни'])} корней, {len(d['исключения'])} исключений")
    print(f"Контрольный набор: {len(d['контрольный_набор']['не_мат'])} безобидных, "
          f"{len(d['контрольный_набор']['мат'])} матерных\n")

    if ложные:
        print(f"❌ ЛОЖНЫЕ СРАБАТЫВАНИЯ ({len(ложные)}): {', '.join(ложные)}")
        print("   Добавь их в «исключения» в slovar.json — они портят доверие к отчёту.")
    else:
        print("✅ Ложных срабатываний нет")

    if пропущенные:
        print(f"⚠️  ПРОПУЩЕНО ({len(пропущенные)}): {', '.join(пропущенные)}")
        print("   Нужен корень в «мат_корни» в slovar.json.")
    else:
        print("✅ Из контрольного мата ничего не пропущено")

    if ложные or пропущенные:
        print("\nПроверка НЕ пройдена — разбор подборки запускать рано.")
        sys.exit(1)
    print("\nПроверка пройдена, навык готов к работе.")


if __name__ == "__main__":
    main()
