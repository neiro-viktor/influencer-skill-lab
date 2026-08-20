#!/usr/bin/env python3
"""Build an auditable shortlist from influencer screening results."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path


OUTPUT_FIELDS = [
    "rank", "candidate_id", "handle", "price_rub", "forecast_views", "cpv_rub",
    "screening_decision", "shortlist_decision", "shortlist_reason", "risk_evidence",
]


def configure_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def number(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def classify(row: dict[str, str], brief: dict) -> tuple[str, str]:
    screening = row["decision"]
    forecast = number(row.get("forecast_views", ""))
    cpv = number(row.get("cpv_rub", ""))
    if screening == "Не рекомендовать без согласования":
        return "Отклонить", "Контентный риск требует отдельного согласования"
    if screening == "Нужна ручная проверка":
        return "Ручная проверка", row.get("reason", "Недостаточно данных")
    if screening == "С оговоркой":
        return "Резерв", row.get("reason", "Есть оговорка первичного скрининга")
    if forecast is None or cpv is None:
        return "Ручная проверка", "Нет прогноза или CPV; факт не восстановлен догадкой"
    if forecast < float(brief["min_forecast_views"]):
        return "Резерв", "Прогноз ниже минимума кампании"
    if cpv > float(brief["max_cpv_rub"]):
        return "Резерв", "CPV выше лимита кампании"
    return "Шорт-лист", "Проходит ограничения кампании"


def build(rows: list[dict[str, str]], brief: dict) -> list[dict]:
    results = []
    for row in rows:
        decision, reason = classify(row, brief)
        results.append({
            "rank": "",
            "candidate_id": row["candidate_id"],
            "handle": row["handle"],
            "price_rub": int(float(row["price_rub"])),
            "forecast_views": row.get("forecast_views", ""),
            "cpv_rub": row.get("cpv_rub", ""),
            "screening_decision": row["decision"],
            "shortlist_decision": decision,
            "shortlist_reason": reason,
            "risk_evidence": row.get("risk_evidence", ""),
        })

    selected = [row for row in results if row["shortlist_decision"] == "Шорт-лист"]
    budget = int(brief["total_budget_rub"])
    total = sum(row["price_rub"] for row in selected)
    for row in sorted(selected, key=lambda item: number(str(item["cpv_rub"])) or 10**9, reverse=True):
        if total <= budget:
            break
        row["shortlist_decision"] = "Резерв"
        row["shortlist_reason"] = "Перенесён в резерв для соблюдения общего бюджета"
        total -= row["price_rub"]

    selected = sorted(
        (row for row in results if row["shortlist_decision"] == "Шорт-лист"),
        key=lambda item: number(str(item["cpv_rub"])) or 10**9,
    )
    for index, row in enumerate(selected, start=1):
        row["rank"] = index
    order = {"Шорт-лист": 0, "Резерв": 1, "Ручная проверка": 2, "Отклонить": 3}
    return sorted(results, key=lambda row: (order[row["shortlist_decision"]], row["rank"] or 999, row["handle"]))


def write(rows: list[dict], brief: dict, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with (output / "shortlist.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    counts = Counter(row["shortlist_decision"] for row in rows)
    budget = sum(row["price_rub"] for row in rows if row["shortlist_decision"] == "Шорт-лист")
    summary = {"campaign": brief["campaign"], "counts": dict(counts), "selected_budget_rub": budget, "budget_limit_rub": brief["total_budget_rub"]}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        f"# Шорт-лист — {brief['campaign']}", "",
        f"Выбранный бюджет: **{budget:,} ₽** из **{brief['total_budget_rub']:,} ₽**.".replace(",", " "), "",
    ]
    for section in ("Шорт-лист", "Резерв", "Ручная проверка", "Отклонить"):
        lines.extend([f"## {section}", ""])
        section_rows = [row for row in rows if row["shortlist_decision"] == section]
        if not section_rows:
            lines.append("Нет кандидатов.")
        for row in section_rows:
            rank = f"{row['rank']}. " if row["rank"] else ""
            lines.append(f"- {rank}**{row['handle']}** — {row['shortlist_reason']} (CPV {row['cpv_rub'] or 'нет данных'} ₽).")
        lines.append("")
    (output / "shortlist.md").write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screening", type=Path, required=True)
    parser.add_argument("--brief", type=Path, default=root / "assets" / "campaign-brief.json")
    parser.add_argument("--output", type=Path, default=Path("output/shortlist"))
    return parser.parse_args()


def main() -> int:
    configure_utf8_output()
    args = parse_args()
    with args.screening.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    with args.brief.open(encoding="utf-8") as handle:
        brief = json.load(handle)
    required = {"candidate_id", "handle", "price_rub", "forecast_views", "cpv_rub", "decision"}
    missing = required - set(rows[0] if rows else {})
    if missing:
        raise SystemExit("В screening.csv нет полей: " + ", ".join(sorted(missing)))
    result = build(rows, brief)
    write(result, brief, args.output)
    print(f"Готово: {len(result)} кандидатов → {args.output.resolve()}")
    print("Решения: " + "; ".join(f"{key} — {value}" for key, value in sorted(Counter(r['shortlist_decision'] for r in result).items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
