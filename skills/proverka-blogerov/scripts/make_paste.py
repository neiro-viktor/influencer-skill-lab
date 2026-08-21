#!/usr/bin/env python3
"""
Готовит блоки для вставки в таблицу клиента.

Колонки идут не подряд: H стоит отдельно, между ней и N лежит красная колонка M
с формулой — её перезаписывать нельзя. Поэтому два блока, а не один.

  блок_H.tsv      → встать на H4, Ctrl+V   (Подписчики)
  блок_N_S.tsv    → встать на N4, Ctrl+V   (Прогноз просмотров, охватов, ER, CPV, Вердикт, Комментарий)
  блок_N_S_кратко.tsv — то же, но комментарий одной строкой, если многострочный разъедется

Числа в русской локали: десятичный разделитель — запятая, иначе Sheets
принимает их за текст и Total перестаёт считаться.
"""
import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def plural(n, one, few, many):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def sp(n):
    """1234567 → «1 234 567»"""
    try:
        return f"{int(round(float(n))):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "—"


def short(url):
    return url.replace("https://www.tiktok.com/", "").replace("https://", "")


def verdict_short(r):
    """Колонка R — коротко, чтобы фильтровать и сортировать."""
    d = r.get("детали") or {}
    if d.get("ошибка"):
        t = str(d["ошибка"])
        if "Unable to extract secondary user ID" in t or "private" in t.lower() or "embedding" in t.lower():
            return "⚠️ ЗАКРЫТЫЙ АККАУНТ — не проверить"
        return "⚠️ ПРОВЕРИТЬ ВРУЧНУЮ"
    мат, бренды = d.get("мат") or [], d.get("бренды") or []
    низко = (d.get("план_просмотров") and r.get("прогноз_просмотров")
             and r["прогноз_просмотров"] < d["план_просмотров"] * 0.8)
    if мат:
        return f"❌ НЕ РЕКОМЕНДУЮ — мат в {len(мат)} из {d.get('роликов', '?')} роликов"
    if бренды:
        brands = sorted({h["бренд"] for v in бренды for h in v["хиты"]})
        return f"⚠️ С ОГОВОРКАМИ — конкуренты в контенте: {', '.join(brands)}"
    if низко:
        delta = (d["план_просмотров"] - r["прогноз_просмотров"]) / d["план_просмотров"] * 100
        return f"⚠️ С ОГОВОРКАМИ — просмотры ниже плана на {delta:.0f}%"
    if d.get("без_текста"):
        return (f"⚠️ НУЖНА РУЧНАЯ ПРОВЕРКА — без распознанной речи "
                f"{d['без_текста']} из {d.get('роликов', '?')} роликов")
    return "✅ ПОДХОДИТ"


def comment(r):
    """Развёрнутый комментарий: вердикт → доказательства с цитатами и ссылками →
    цифры с разбросом → что делать. Менеджер должен понять решение без открытия роликов,
    но иметь возможность проверить каждую строку."""
    d = r.get("детали") or {}
    if d.get("ошибка"):
        t = str(d["ошибка"])
        if "Unable to extract secondary user ID" in t or "private" in t.lower() or "embedding" in t.lower():
            return ("Профиль закрыт или запрещено встраивание — историю роликов не проверить "
                    "ни вручную, ни машиной.\n"
                    "ЧТО ДЕЛАТЬ: запросить у блогера статистику скриншотами из кабинета "
                    "либо исключить из подборки — риск непроверяемый.")
        if "404" in t or "not found" in t.lower():
            return "Аккаунт не найден — проверить ссылку в колонке G."
        return f"⚠️ Проверить вручную: {t}"

    мат, бренды = d.get("мат") or [], d.get("бренды") or []
    блоки = []

    if мат:
        строки = [f"МАТ: в {len(мат)} {plural(len(мат), 'ролике', 'роликах', 'роликах')} из {d.get('роликов', '?')}"]
        for v in мат[:3]:
            for h in v["хиты"][:2]:
                фраза = h["фраза"][:70]
                строки.append(f"• [{h['время']}] «{h['слово']}» — «{фраза}»")
            строки.append(f"  {short(v['url'])}")
        блоки.append("\n".join(строки))

    if бренды:
        строки = [f"БРЕНДЫ КОНКУРЕНТОВ: {', '.join(sorted({h['бренд'] for v in бренды for h in v['хиты']}))}"]
        for v in бренды[:3]:
            for h in v["хиты"][:2]:
                где = h["где"]
                фраза = h.get("фраза", "—")
                строки.append(f"• {h['бренд']} ({где})" + (f": «{фраза[:60]}»" if фраза != "—" else ""))
            строки.append(f"  {short(v['url'])}")
        блоки.append("\n".join(строки))

    # Показатели
    строки = [f"ПОКАЗАТЕЛИ (по {d.get('роликов', '?')} последним роликам"
              + (f", исключено «залетевших» {d['выбросов']}" if d.get("выбросов") else "") + "):"]
    if r.get("прогноз_просмотров"):
        s = f"• Просмотры на публикацию: {sp(r['прогноз_просмотров'])}"
        if d.get("план_просмотров"):
            delta = (r["прогноз_просмотров"] - d["план_просмотров"]) / d["план_просмотров"] * 100
            знак = "ниже" if delta < 0 else "выше"
            s += f" (в плане стояло {sp(d['план_просмотров'])} — {знак} на {abs(delta):.0f}%)"
        строки.append(s)
    if d.get("разброс"):
        строки.append(f"• Разброс по роликам: от {sp(d['разброс'][0])} до {sp(d['разброс'][1])}")
    if r.get("подписчики") and r.get("прогноз_просмотров"):
        доля = r["прогноз_просмотров"] / r["подписчики"] * 100
        строки.append(f"• Подписчиков {sp(r['подписчики'])} → смотрит около {доля:.0f}% базы")
    if d.get("среднее_реакций"):
        строки.append(f"• Реакций на ролик: {sp(d['среднее_реакций'])} "
                      f"(ER {d.get('er', 0):.1f}% от подписчиков, ERViews {d.get('er_views', 0):.1f}% от просмотров)")
    if r.get("CPV"):
        s = f"• CPV по факту {r['CPV']}"
        if d.get("план_cpv"):
            s += f" против {d['план_cpv']} в плане"
        строки.append(s)
    if d.get("без_текста"):
        строки.append(f"• Роликов без распознанной речи: {d['без_текста']} — в них речь не проверялась, отсмотреть глазами")
    блоки.append("\n".join(строки))

    # Что делать
    дела = []
    if мат:
        дела.append("согласовать мат с клиентом или заменить блогера")
    if бренды:
        дела.append("проверить конфликт с конкурентами по договору")
    if d.get("план_просмотров") and r.get("прогноз_просмотров") and \
       r["прогноз_просмотров"] < d["план_просмотров"] * 0.8:
        дела.append("пересчитать бюджет или торговаться по цене — фактические просмотры ниже плана")
    if d.get("без_текста"):
        дела.append("ролики без речи отсмотреть глазами")
    if not дела:
        дела.append("можно брать по плану")
    блоки.append("ЧТО ДЕЛАТЬ: " + "; ".join(дела) + ".")

    return "\n\n".join(блоки)


def one_line(text):
    return " ".join(str(text).replace("\t", " ").split())


def number(value):
    """«4.79 ₽»/«3,8%» → число для Google Sheets."""
    match = re.search(r"-?[\d.,]+", str(value or ""))
    return float(match.group().replace(",", ".")) if match else ""


def build_outputs(res, output):
    output.mkdir(parents=True, exist_ok=True)
    h_lines, nr_lines, nr_short, writes = [], [], [], []
    for index, r in enumerate(res):
        source_row = int(r.get("source_row") or index + 4)
        followers = r.get("подписчики") or ""
        forecast = r.get("прогноз_просмотров") or ""
        reach = r.get("прогноз_охватов") or ""
        er_number = number(r.get("ER"))
        er_fraction = er_number / 100 if er_number != "" else ""
        cpv_number = number(r.get("CPV"))
        er_tsv = str(r.get("ER") or "").replace(".", ",")
        cpv_tsv = str(r.get("CPV") or "").replace(".", ",").replace(" ₽", "")
        verdict = verdict_short(r)
        detailed_comment = comment(r)

        h_lines.append(str(followers))
        row = [str(forecast), str(reach), er_tsv, cpv_tsv, verdict]
        nr_lines.append("\t".join(row + ['"' + detailed_comment.replace('"', "'") + '"']))
        nr_short.append("\t".join(row + [one_line(detailed_comment)]))
        writes.append({
            "source_row": source_row,
            "profile": r.get("ссылка") or r.get("ник"),
            "followers_range": f"H{source_row}",
            "analysis_range": f"N{source_row}:S{source_row}",
            "followers": followers,
            "analysis_values": [forecast, reach, er_fraction, cpv_number, verdict, detailed_comment],
        })

    (output / "блок_H.tsv").write_text("\n".join(h_lines) + "\n", encoding="utf-8")
    (output / "блок_N_S.tsv").write_text("\n".join(nr_lines) + "\n", encoding="utf-8")
    (output / "блок_N_S_кратко.tsv").write_text("\n".join(nr_short) + "\n", encoding="utf-8")
    plan = {
        "sheet_name": next((r.get("sheet_name") for r in res if r.get("sheet_name")),
                           "Несогласованные блогеры"),
        "copy_required": True,
        "write_only_to_participant_copy": True,
        "profiles_processed": len(res),
        "writes": writes,
    }
    (output / "sheet-write-plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return plan


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=HERE / "sheet_out" / "результат.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.input.parent
    res = json.loads(args.input.read_text(encoding="utf-8"))
    plan = build_outputs(res, output)

    print(f"Строк: {len(res)}")
    print(f"  {output / 'sheet-write-plan.json'} → точный план H и N:S для Google-коннектора")
    print(f"  {output / 'блок_H.tsv'} → резервная ручная вставка в H первой строки")
    print(f"  {output / 'блок_N_S.tsv'} → резервная ручная вставка в N первой строки")
    print(f"Диапазоны: {plan['writes'][0]['followers_range'] if plan['writes'] else '—'} … "
          f"{plan['writes'][-1]['analysis_range'] if plan['writes'] else '—'}")
    for r in res:
        if (r.get("детали") or {}).get("мат"):
            print("\nПРИМЕР ГЛУБОКОГО КОММЕНТАРИЯ\n")
            print("R:", verdict_short(r))
            print("\nS:\n" + comment(r))
            break


if __name__ == "__main__":
    main()
