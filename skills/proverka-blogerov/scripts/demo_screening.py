#!/usr/bin/env python3
"""Deterministic influencer screening for the classroom demo."""

from __future__ import annotations

import argparse
import csv
import io
import json
from collections import Counter
from pathlib import Path
from statistics import median
from urllib.request import Request, urlopen


FIELDS = [
    "candidate_id", "handle", "platform", "followers", "price_rub",
    "videos_checked", "transcript_coverage_pct", "median_views",
    "outliers_removed", "average_views_clean", "forecast_views",
    "forecast_reach", "er_pct", "cpv_rub", "decision", "reason",
    "risk_evidence", "source_mode",
]


def read_candidates(local_path: Path, sheet_url: str | None) -> tuple[list[dict[str, str]], str]:
    if sheet_url:
        try:
            request = Request(sheet_url, headers={"User-Agent": "InfluencerSkillLab/1.0"})
            with urlopen(request, timeout=12) as response:
                payload = response.read().decode("utf-8-sig")
            rows = list(csv.DictReader(io.StringIO(payload)))
            if not rows:
                raise ValueError("public sheet returned no rows")
            return rows, "public-sheet"
        except Exception as exc:  # network is an optional classroom branch
            print(f"Публичная таблица недоступна ({exc}); использую локальный резерв.")
    with local_path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle)), "local-demo"


def average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def screen(row: dict[str, str], evidence: dict) -> dict:
    videos = evidence.get("videos", [])
    views = [int(video["views"]) for video in videos if video.get("views") is not None]
    med = median(views) if views else 0
    clean_videos = [video for video in videos if int(video.get("views", 0)) <= 3 * med] if med else []
    avg_views = average([int(video["views"]) for video in clean_videos])
    forecast = avg_views * 0.9
    reach = forecast / 1.15 if forecast else 0
    followers = int(row["followers"])
    interactions = [
        int(video.get("likes", 0)) + int(video.get("comments", 0)) + int(video.get("shares", 0))
        for video in clean_videos
    ]
    er = average(interactions) / followers * 100 if followers else 0
    price = int(row["price_rub"])
    cpv = price / forecast if forecast else 0
    coverage = float(evidence.get("transcript_coverage", 0))
    profanity = evidence.get("profanity_hits", [])
    competitors = evidence.get("competitor_hits", [])
    target = int(row["target_forecast_views"])

    if len(videos) < 5 or coverage < 0.8:
        decision = "Нужна ручная проверка"
        reason = "Недостаточно роликов или расшифровки"
    elif profanity:
        decision = "Не рекомендовать без согласования"
        reason = "Обнаружена подтверждённая ненормативная лексика"
    elif competitors:
        decision = "Нужна ручная проверка"
        reason = "Обнаружено упоминание конкурента"
    elif forecast < target * 0.75:
        decision = "С оговоркой"
        reason = "Прогноз ниже 75% целевого уровня"
    else:
        decision = "Подходит"
        reason = "Метрики и проверенный контент проходят учебные правила"

    risk_evidence = profanity + competitors
    return {
        "candidate_id": row["candidate_id"],
        "handle": row["handle"],
        "platform": row["platform"],
        "followers": followers,
        "price_rub": price,
        "videos_checked": len(videos),
        "transcript_coverage_pct": round(coverage * 100, 1),
        "median_views": round(med),
        "outliers_removed": len(videos) - len(clean_videos),
        "average_views_clean": round(avg_views),
        "forecast_views": round(forecast),
        "forecast_reach": round(reach),
        "er_pct": round(er, 2),
        "cpv_rub": round(cpv, 2) if forecast else "",
        "decision": decision,
        "reason": reason,
        "risk_evidence": " | ".join(
            f"ролик {item['video']}, {item['timestamp']}: {item['text']}" for item in risk_evidence
        ),
    }


def write_results(rows: list[dict], output: Path, source_mode: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    enriched = [{**row, "source_mode": source_mode} for row in rows]
    with (output / "screening.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(enriched)
    with (output / "screening.json").open("w", encoding="utf-8") as handle:
        json.dump({"source_mode": source_mode, "records": enriched}, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    decisions = Counter(row["decision"] for row in enriched)
    report = [
        "# Результат проверки блогеров",
        "",
        f"Источник: `{source_mode}`. Обработано кандидатов: **{len(enriched)}**.",
        "",
        "## Решения",
        "",
    ]
    report.extend(f"- {name}: {count}" for name, count in sorted(decisions.items()))
    report.extend(["", "## Кандидаты", ""])
    report.extend(
        f"- **{row['handle']}** — {row['decision']}; прогноз {row['forecast_views']} просмотров; "
        f"CPV {row['cpv_rub'] or 'нет данных'} ₽. {row['reason']}."
        for row in enriched
    )
    (output / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    skill_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=skill_root / "assets" / "bloggers.csv")
    parser.add_argument("--evidence", type=Path, default=skill_root / "assets" / "evidence.json")
    parser.add_argument("--sheet-url")
    parser.add_argument("--output", type=Path, default=Path("output/screening"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    candidates, source_mode = read_candidates(args.input, args.sheet_url)
    with args.evidence.open(encoding="utf-8") as handle:
        evidence = json.load(handle)
    missing = [row["candidate_id"] for row in candidates if row["candidate_id"] not in evidence]
    if missing:
        raise SystemExit("Нет доказательств для: " + ", ".join(missing))
    results = [screen(row, evidence[row["candidate_id"]]) for row in candidates]
    write_results(results, args.output, source_mode)
    print(f"Готово: {len(results)} кандидатов → {args.output.resolve()}")
    print("Решения: " + "; ".join(f"{key} — {value}" for key, value in sorted(Counter(r['decision'] for r in results).items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
