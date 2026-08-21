#!/usr/bin/env python3
"""Collect a quick, evidenced snapshot of public influencer profiles."""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

OUTPUT_FIELDS = [
    "participant", "handle", "platform", "profile_url", "source_row", "captured_at_utc",
    "followers", "videos_opened", "median_views", "outliers_removed",
    "average_views_clean", "forecast_views", "forecast_reach", "er_pct",
    "price_rub", "cpv_rub", "target_forecast_views", "decision", "reason",
    "speech_check", "evidence_urls",
]

ALIASES = {
    "participant": ("participant", "участник", "менеджер", "автор"),
    "profile_url": ("profile_url", "ссылка", "ссылка на блогера", "ссылка на аккаунт", "профиль", "url"),
    "handle": ("handle", "ник", "никнейм", "блогер"),
    "price_rub": ("price_rub", "цена", "стоимость", "цена публикации", "стоимость одной публикации"),
    "target_forecast_views": (
        "target_forecast_views", "прогноз просмотров", "прогноз просмотров по всем публикациям",
        "план просмотров", "целевые просмотры",
    ),
}


def configure_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def normalized(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower().replace("ё", "е"))


def number(value: str | int | float | None) -> float | None:
    if value is None or value == "":
        return None
    match = re.search(r"[\d.,]+", str(value).replace("\xa0", "").replace(" ", ""))
    if not match:
        return None
    try:
        return float(match.group().replace(",", "."))
    except ValueError:
        return None


def google_csv_url(value: str) -> str:
    parsed = urlparse(value)
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", parsed.path)
    if not match:
        return value
    gid = (parse_qs(parsed.query).get("gid") or parse_qs(parsed.fragment).get("gid") or [""])[0]
    suffix = f"&gid={gid}" if gid else ""
    return f"https://docs.google.com/spreadsheets/d/{match.group(1)}/export?format=csv{suffix}"


def fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": "InfluencerSkillLab/1.1"})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8-sig")


def header_index(rows: list[list[str]]) -> int:
    wanted = {normalized(alias) for values in ALIASES.values() for alias in values}
    scored = []
    for index, row in enumerate(rows[:20]):
        score = sum(1 for cell in row if normalized(cell) in wanted or "ссыл" in normalized(cell))
        scored.append((score, index))
    return max(scored, default=(0, 0))[1]


def field_map(headers: list[str]) -> dict[str, int]:
    result = {}
    for field, aliases in ALIASES.items():
        normalized_aliases = {normalized(alias) for alias in aliases}
        for index, header in enumerate(headers):
            if normalized(header) in normalized_aliases:
                result[field] = index
                break
    return result


def cell(row: list[str], index: int | None) -> str:
    return row[index].strip() if index is not None and index < len(row) else ""


def extract_profile_url(row: list[str], preferred: int | None) -> str:
    candidate = cell(row, preferred)
    if candidate.startswith("http"):
        return candidate
    return next((value.strip() for value in row if value.strip().startswith("http")), "")


def read_candidates(path: Path | None, sheet_url: str | None, max_profiles: int) -> list[dict[str, str]]:
    if sheet_url:
        payload = fetch_text(google_csv_url(sheet_url))
    elif path:
        payload = path.read_text(encoding="utf-8-sig")
    else:
        raise ValueError("Нужен CSV-файл или ссылка на Google-таблицу")
    rows = list(csv.reader(io.StringIO(payload)))
    if not rows:
        return []
    start = header_index(rows)
    mapping = field_map(rows[start])
    candidates = []
    for source_row, row in enumerate(rows[start + 1 :], start=start + 2):
        profile_url = extract_profile_url(row, mapping.get("profile_url"))
        if not profile_url:
            continue
        candidates.append({
            "participant": cell(row, mapping.get("participant")),
            "handle": cell(row, mapping.get("handle")),
            "profile_url": profile_url,
            "price_rub": cell(row, mapping.get("price_rub")),
            "target_forecast_views": cell(row, mapping.get("target_forecast_views")),
            "source_row": str(source_row),
        })
        if len(candidates) >= max_profiles:
            break
    return candidates


def platform_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "tiktok" in host:
        return "TikTok"
    if "youtube" in host or "youtu.be" in host:
        return "YouTube"
    if "vk.com" in host:
        return "VK"
    return host or "не определена"


def listing_url(url: str) -> str:
    parsed = urlparse(url)
    if platform_of(url) == "YouTube" and "/@" in parsed.path and not parsed.path.rstrip("/").endswith(("/videos", "/shorts")):
        return url.rstrip("/") + "/videos"
    return url


def handle_of(candidate: dict[str, str]) -> str:
    if candidate.get("handle"):
        return candidate["handle"]
    match = re.search(r"@([\w.\-]+)", candidate["profile_url"])
    return f"@{match.group(1)}" if match else candidate["profile_url"]


def tiktok_followers(url: str) -> int | None:
    try:
        from curl_cffi import requests

        response = requests.get(url, impersonate="chrome", timeout=25)
        match = re.search(r'"followerCount":(\d+)', response.text)
        return int(match.group(1)) if match else None
    except Exception:
        return None


def collect(candidate: dict[str, str], limit: int) -> tuple[dict, list[dict]]:
    from yt_dlp import YoutubeDL

    profile_url = candidate["profile_url"]
    quiet = {"quiet": True, "no_warnings": True, "socket_timeout": 35, "skip_download": True}
    with YoutubeDL({**quiet, "extract_flat": True, "playlistend": limit}) as ydl:
        profile = ydl.extract_info(listing_url(profile_url), download=False)
    entries = list((profile or {}).get("entries") or [])[:limit]
    videos = []
    followers = (profile or {}).get("channel_follower_count")
    for entry in entries:
        video_url = entry.get("webpage_url") or entry.get("url")
        if not video_url:
            continue
        try:
            with YoutubeDL(quiet) as ydl:
                metadata = ydl.extract_info(video_url, download=False)
            followers = followers or metadata.get("channel_follower_count")
            videos.append({
                "url": metadata.get("webpage_url") or video_url,
                "upload_date": metadata.get("upload_date"),
                "views": int(metadata.get("view_count") or 0),
                "likes": int(metadata.get("like_count") or 0),
                "comments": int(metadata.get("comment_count") or 0),
                "shares": int(metadata.get("repost_count") or 0),
            })
        except Exception as exc:
            videos.append({"url": video_url, "error": f"{type(exc).__name__}: {str(exc)[:120]}"})
    if not followers and platform_of(profile_url) == "TikTok":
        followers = tiktok_followers(profile_url)
    return {"followers": followers}, videos


def calculate(candidate: dict[str, str], profile: dict, videos: list[dict], captured_at: str) -> dict:
    opened = [video for video in videos if not video.get("error") and video.get("views")]
    handle = handle_of(candidate)
    if not opened:
        reason = videos[0].get("error", "публичные ролики не открылись") if videos else "профиль не отдал ролики"
        return {
            **{field: "" for field in OUTPUT_FIELDS},
            "participant": candidate.get("participant", ""), "handle": handle,
            "platform": platform_of(candidate["profile_url"]), "profile_url": candidate["profile_url"],
            "source_row": candidate.get("source_row", ""),
            "captured_at_utc": captured_at, "decision": "Проверить вручную", "reason": reason,
            "speech_check": "не проверялась", "evidence_urls": "",
        }
    views = [video["views"] for video in opened]
    median_views = statistics.median(views)
    clean = [video for video in opened if video["views"] <= 3 * median_views] or opened
    average_views = statistics.mean(video["views"] for video in clean)
    forecast = average_views * 0.9
    reach = forecast / 1.15 if forecast else 0
    followers = number(profile.get("followers"))
    reactions = [video["likes"] + video["comments"] + video["shares"] for video in clean]
    er = statistics.mean(reactions) / followers * 100 if followers else None
    price = number(candidate.get("price_rub"))
    target = number(candidate.get("target_forecast_views"))
    cpv = price / forecast if price and forecast else None
    if er is not None and er > 100:
        decision = "Брать с оговорками"
        reason = "ER к базе подписчиков выше 100% — возможен вирусный эффект; проверить на 10 роликах"
    elif target and forecast < target * 0.75:
        decision = "Брать с оговорками"
        reason = f"быстрый прогноз ниже цели на {(1 - forecast / target) * 100:.0f}%"
    else:
        decision = "Предварительно подходит"
        reason = "открытые метрики собраны; контентные риски требуют отдельного глубокого прохода"
    return {
        "participant": candidate.get("participant", ""), "handle": handle,
        "platform": platform_of(candidate["profile_url"]), "profile_url": candidate["profile_url"],
        "source_row": candidate.get("source_row", ""),
        "captured_at_utc": captured_at, "followers": int(followers) if followers else "",
        "videos_opened": len(opened), "median_views": round(median_views),
        "outliers_removed": len(opened) - len(clean), "average_views_clean": round(average_views),
        "forecast_views": round(forecast), "forecast_reach": round(reach),
        "er_pct": round(er, 2) if er is not None else "", "price_rub": int(price) if price else "",
        "cpv_rub": round(cpv, 2) if cpv is not None else "",
        "target_forecast_views": int(target) if target else "", "decision": decision, "reason": reason,
        "speech_check": "не проверялась в быстром live-режиме",
        "evidence_urls": " | ".join(video["url"] for video in opened),
    }


def onegroup_verdict(row: dict) -> str:
    if row.get("decision") == "Проверить вручную":
        return "⚠️ ПРОВЕРИТЬ ВРУЧНУЮ — профиль не отдал метрики"
    if row.get("decision") == "Брать с оговорками":
        return "⚠️ С ОГОВОРКОЙ — прогноз ниже цели"
    return "⚠️ ПРЕДВАРИТЕЛЬНО ПОДХОДИТ — контент проверить отдельно"


def onegroup_comment(row: dict) -> str:
    if not row.get("videos_opened"):
        return f"LIVE-ПРОВЕРКА НЕ ЗАВЕРШЕНА. {row.get('reason', '')} ЧТО ДЕЛАТЬ: проверить профиль вручную или взять другую ссылку."
    cpv = f"{row['cpv_rub']} ₽" if row.get("cpv_rub") != "" else "не рассчитан — нет стоимости"
    evidence = row.get("evidence_urls") or "нет"
    return (
        f"QUICK-LIVE: открыто роликов — {row['videos_opened']}; прогноз просмотров — {row['forecast_views']}; "
        f"прогноз охвата — {row['forecast_reach']}; ER — {row['er_pct']}%; CPV — {cpv}; "
        f"исключено выбросов — {row['outliers_removed']}. Речь, мат, конкуренты и кадры в быстром режиме "
        f"не проверялись. ЧТО ДЕЛАТЬ: перед финальным согласованием выполнить глубокий проход по 10 роликам. "
        f"ДОКАЗАТЕЛЬСТВА: {evidence}"
    )


def write_onegroup_sheet_artifacts(results: list[dict], output: Path, sheet_url: str | None) -> None:
    rows = [row for row in results if str(row.get("source_row", "")).isdigit()]
    writes = []
    for row in rows:
        source_row = int(row["source_row"])
        er_fraction = round(float(row["er_pct"]) / 100, 6) if row.get("er_pct") != "" else ""
        writes.append({
            "source_row": source_row,
            "followers_range": f"H{source_row}",
            "analysis_range": f"N{source_row}:S{source_row}",
            "followers": row.get("followers", ""),
            "analysis_values": [
                row.get("forecast_views", ""), row.get("forecast_reach", ""), er_fraction,
                row.get("cpv_rub", ""), onegroup_verdict(row), onegroup_comment(row),
            ],
        })
    plan = {
        "source_spreadsheet_url": sheet_url or "",
        "sheet_name": "Несогласованные блогеры",
        "copy_required": True,
        "write_only_to_participant_copy": True,
        "columns": {"H": "Подписчики", "N": "Прогноз просмотров", "O": "Прогноз охватов", "P": "ER", "Q": "CPV", "R": "Вердикт", "S": "Комментарий клиента"},
        "writes": writes,
    }
    (output / "sheet-write-plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (output / "for-onegroup-H.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerows([[item["followers"]] for item in writes])
    with (output / "for-onegroup-N-S.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerows(item["analysis_values"] for item in writes)


def write_outputs(results: list[dict], evidence: list[dict], output: Path, sheet_url: str | None = None) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with (output / "live-results.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(results)
    (output / "live-results.json").write_text(
        json.dumps({"records": results, "evidence": evidence}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    paste_fields = ["participant", "handle", "profile_url", "followers", "forecast_views", "er_pct", "cpv_rub", "decision", "reason", "captured_at_utc"]
    with (output / "for-sheet.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=paste_fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in paste_fields} for row in results)
    report = [
        "# Быстрая проверка реальных профилей", "",
        f"Проверено профилей: **{len(results)}**. Снимок сделан: `{results[0]['captured_at_utc'] if results else ''}`.", "",
        "## Результат", "",
    ]
    report.extend(
        f"- **{row['handle']}** — {row['decision']}; подписчики: {row['followers'] or 'нет данных'}; "
        f"прогноз просмотров: {row['forecast_views'] or 'нет данных'}; CPV: {row['cpv_rub'] or 'не рассчитан'}. {row['reason']}."
        for row in results
    )
    report.extend([
        "", "## Важно", "",
        "Это актуальный снимок открытых метрик, а не синтетическое демо. В быстром режиме речь и кадры не проверяются.",
        "Для рабочего решения перезапустите профиль по 10 роликам и отдельно выполните глубокий контентный проход.",
    ])
    (output / "live-report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    if sheet_url:
        write_onegroup_sheet_artifacts(results, output, sheet_url)


def main() -> int:
    configure_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="CSV со ссылками на публичные профили")
    source.add_argument("--sheet-url", help="Публичная ссылка Google Sheets")
    source.add_argument("--profile", action="append", help="Публичный профиль; можно повторить несколько раз")
    parser.add_argument("--limit", type=int, default=3, help="Роликов на профиль для быстрого снимка")
    parser.add_argument("--max-profiles", type=int, default=2, help="Максимум профилей за один учебный прогон")
    parser.add_argument("--output", type=Path, default=Path("output/live-screening"))
    args = parser.parse_args()
    if not 1 <= args.limit <= 20 or not 1 <= args.max_profiles <= 50:
        parser.error("limit должен быть 1–20, max-profiles — 1–50")
    try:
        candidates = (
            [{"participant": "", "handle": "", "profile_url": url, "price_rub": "", "target_forecast_views": ""}
             for url in (args.profile or [])[: args.max_profiles]]
            if args.profile else read_candidates(args.input, args.sheet_url, args.max_profiles)
        )
    except Exception as exc:
        print(f"Не удалось прочитать вход: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if not candidates:
        print("Во входе не найдено ни одной публичной ссылки на профиль.", file=sys.stderr)
        return 2
    captured_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    results, evidence = [], []
    for index, candidate in enumerate(candidates, 1):
        print(f"[{index}/{len(candidates)}] Проверяю {handle_of(candidate)}…", flush=True)
        try:
            profile, videos = collect(candidate, args.limit)
        except Exception as exc:
            profile, videos = {}, [{"error": f"{type(exc).__name__}: {str(exc)[:180]}"}]
        results.append(calculate(candidate, profile, videos, captured_at))
        evidence.append({"profile_url": candidate["profile_url"], "videos": videos})
    write_outputs(results, evidence, args.output, args.sheet_url)
    print(f"Готово: {len(results)} профилей → {args.output.resolve()}")
    print("Созданы live-results.csv, live-results.json, live-report.md и for-sheet.tsv")
    if args.sheet_url:
        print("Для копии таблицы созданы sheet-write-plan.json и два блока точной вставки H / N:S")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
