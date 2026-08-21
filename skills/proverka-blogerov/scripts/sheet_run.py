#!/usr/bin/env python3
"""
Прогон подборки блогеров из Google-таблицы.

Читает таблицу публичным экспортом, по каждому блогеру снимает подписчиков и
последние ролики, считает факт против прогноза, ищет мат и конкурентов,
формирует готовые значения для колонок «Подписчики» и «Комментарий клиента».

    python3 sheet_run.py --sheet <ID> --videos 10 --brief brief.json
"""
import argparse, csv, io, json, os, re, statistics, subprocess, sys, threading, time, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_roller as cr

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

YTDLP = os.environ.get("YTDLP_BIN", "yt-dlp")
FFMPEG = os.environ.get("FFMPEG_BIN")
WHISPER_LOCK = threading.Lock()
COL = {"ник": 5, "ссылка": 6, "подписчики": 7, "цена": 9, "прогноз_просмотров": 13,
       "er": 15, "cpv": 16, "комментарий": 17}


def num(s):
    s = (s or "").replace("\xa0", "").replace(" ", "").replace("₽", "").replace(",", ".")
    m = re.search(r"[\d.]+", s)
    return float(m.group()) if m else None


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def ytdlp(*args):
    command = [YTDLP]
    if FFMPEG:
        command.extend(["--ffmpeg-location", FFMPEG])
    command.extend(args)
    return run(command)


def tiktok_followers(url):
    """yt-dlp по TikTok подписчиков не отдаёт — берём их со страницы профиля."""
    try:
        from curl_cffi import requests
        r = requests.get(url, impersonate="chrome", timeout=25)
        m = re.search(r'"followerCount":(\d+)', r.text)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def video_text(video_id, url, workdir):
    """Субтитры площадки, а при их отсутствии — скачивание аудио и локальный Whisper."""
    base = str(workdir / video_id)

    def subtitle_files():
        def rank(path):
            lang = path.name.split(".")[-2].lower()
            return (0 if lang.startswith("rus") or lang == "ru" else 1, lang)
        return sorted(workdir.glob(f"{video_id}*.vtt"), key=rank)

    if not subtitle_files():
        ytdlp("--socket-timeout", "45", "--skip-download", "--write-subs", "--write-auto-subs",
               "--sub-langs", "rus-RU,ru", "--convert-subs", "vtt", "-o", base, url)
    subs = subtitle_files()
    if subs:
        full_text, cues = cr.vtt_to_text(subs[0])
        if cues and full_text.strip():
            return cues, "субтитры"

    audio = workdir / f"{video_id}.mp3"
    if not audio.exists():
        ytdlp("--socket-timeout", "90", "-x", "--audio-format", "mp3",
               "-o", base + ".%(ext)s", url)
    if not audio.exists():
        return [], "аудио не скачалось"
    print(f"  ↳ локально распознаю речь: {url}", flush=True)
    with WHISPER_LOCK:
        _, cues, error = cr.transcribe_local(audio)
    if error:
        return [], f"ошибка распознавания: {error}"
    return cues, "локальное распознавание"


def profile_stats(url, n_videos, brief, workdir):
    """Подписчики + метрики и риски по последним роликам."""
    p = ytdlp("--socket-timeout", "45", "--flat-playlist", "--dump-json",
              "--playlist-items", f"1-{n_videos}", url)
    items = []
    for line in p.stdout.splitlines():
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    if not items:
        err = (p.stderr or "нет данных").strip().splitlines()[-1][:110]
        if "private" in err.lower() or "embedding" in err.lower():
            err = "закрытый аккаунт — проверить историю невозможно, запросить статистику у блогера"
        return {"ошибка": err, "подписчики": tiktok_followers(url)}

    followers = next((i.get("channel_follower_count") for i in items
                      if i.get("channel_follower_count")), None) or tiktok_followers(url)

    vids = []
    for it in items:
        vurl = it.get("url") or it.get("webpage_url")
        if not vurl:
            continue
        mp = ytdlp("--socket-timeout", "45", "--skip-download", "--dump-single-json", vurl)
        if mp.returncode != 0:
            continue
        m = json.loads(mp.stdout)
        followers = followers or m.get("channel_follower_count")
        cues, source = video_text(m["id"], vurl, workdir)
        vids.append({
            "url": vurl,
            "просмотры": m.get("view_count") or 0,
            "лайки": m.get("like_count") or 0,
            "комментарии": m.get("comment_count") or 0,
            "репосты": m.get("repost_count") or 0,
            "мат": cr.find_profanity(cues),
            "конкуренты": cr.find_competitors(cues, brief.get("конкуренты", {}), m.get("description") or ""),
            "есть_текст": bool(cues),
            "источник_речи": source,
        })
        time.sleep(1)

    if not vids:
        return {"ошибка": "ролики не открылись", "подписчики": followers}

    # Методика Digital (Катя Ревякина, 31.07):
    #   просмотры = среднее за последние 10 роликов без выбросов, минус 10%
    #   охваты    = просмотры / 1,15
    #   ER        = (лайки + комментарии + репосты) / подписчики
    #   ERViews   = реакции ролика / просмотры этого ролика (справочно)
    #   CPV       = цена публикации / просмотры
    with_views = [v for v in vids if v["просмотры"]]
    views = [v["просмотры"] for v in with_views]
    med_all = statistics.median(views) if views else 0
    base = [v for v in with_views if not (med_all and v["просмотры"] > 3 * med_all)] or with_views

    avg_views = statistics.mean([v["просмотры"] for v in base]) if base else 0
    forecast_views = avg_views * 0.9
    forecast_reach = forecast_views / 1.15 if forecast_views else 0
    reactions = [v["лайки"] + v["комментарии"] + v["репосты"] for v in base]
    avg_react = statistics.mean(reactions) if reactions else 0
    er = (avg_react / followers * 100) if followers else 0
    er_views = (statistics.mean([(v["лайки"] + v["комментарии"] + v["репосты"]) / v["просмотры"] * 100
                                 for v in base]) if base else 0)
    return {
        "подписчики": followers,
        "роликов": len(vids),
        "среднее_без_выбросов": avg_views,
        "прогноз_просмотров": forecast_views,
        "прогноз_охватов": forecast_reach,
        "er": er,
        "er_views": er_views,
        "среднее_реакций": avg_react,
        "выбросов": len(with_views) - len(base),
        "разброс": [min(v["просмотры"] for v in base), max(v["просмотры"] for v in base)] if base else None,
        "мат_ролики": [v for v in vids if v["мат"]],
        "конкур_ролики": [v for v in vids if v["конкуренты"]],
        "без_текста": sum(1 for v in vids if not v["есть_текст"]),
        "ролики_детали": [{
            "url": v["url"],
            "просмотры": v["просмотры"],
            "источник_речи": v["источник_речи"],
            "речь_проверена": v["есть_текст"],
        } for v in vids],
    }


def verdict(st, row):
    """Комментарий в ячейку: стоп-факторы, потом база расчёта и расхождение с планом."""
    if "ошибка" in st:
        return f"⚠️ Проверить вручную: {st['ошибка']}"

    parts, flags = [], []
    if st["мат_ролики"]:
        words = sorted({h["слово"] for v in st["мат_ролики"] for h in v["мат"]})
        stamps = [h["время"] for v in st["мат_ролики"] for h in v["мат"]][:3]
        parts.append(f"❌ МАТ в {len(st['мат_ролики'])} из {st['роликов']} роликов "
                     f"({', '.join(words[:3])}; {', '.join(stamps)})")
        flags.append("мат")
    if st["конкур_ролики"]:
        brands = sorted({h["бренд"] for v in st["конкур_ролики"] for h in v["конкуренты"]})
        parts.append(f"⚠️ Бренды в контенте: {', '.join(brands)}")
        flags.append("бренды")

    parts.append(f"Расчёт по {st['роликов'] - st['выбросов']} роликам"
                 + (f", исключено «залетевших» {st['выбросов']}" if st["выбросов"] else ""))

    was = num(row[COL["прогноз_просмотров"]])
    now = st["прогноз_просмотров"]
    if was and now:
        delta = (now - was) / was * 100
        if abs(delta) >= 20:
            arrow = "🔴 ниже" if delta < 0 else "🟢 выше"
            parts.append(f"{arrow} прежнего прогноза на {abs(delta):.0f}% "
                         f"({was:,.0f} → {now:,.0f})".replace(",", " "))
            if delta < 0:
                flags.append("недобор")
    if st.get("er_views"):
        parts.append(f"ERViews {st['er_views']:.1f}%")
    if st.get("без_текста"):
        parts.append(f"без распознанной речи: {st['без_текста']}")

    if "мат" in flags:
        head = "НЕ РЕКОМЕНДУЮ без согласования"
    elif "недобор" in flags or "бренды" in flags:
        head = "БРАТЬ С ОГОВОРКАМИ"
    else:
        head = "ПОДХОДИТ"
    return f"{head}. " + ". ".join(parts) + "."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", required=True)
    ap.add_argument("--videos", type=int, default=10)
    ap.add_argument("--max-profiles", type=int, default=0,
                    help="0 — вся таблица; положительное число — только первые N строк для диагностики")
    ap.add_argument("--sheet-name", default="Несогласованные блогеры")
    ap.add_argument("--brief", type=Path)
    ap.add_argument("--workdir", type=Path, default=HERE / "sheet_out")
    args = ap.parse_args()

    brief = json.loads(args.brief.read_text(encoding="utf-8")) if args.brief else {}
    args.workdir.mkdir(parents=True, exist_ok=True)

    data = urllib.request.urlopen(
        f"https://docs.google.com/spreadsheets/d/{args.sheet}/export?format=csv", timeout=30
    ).read().decode("utf-8")
    rows = list(csv.reader(io.StringIO(data)))
    targets = [(source_row, r) for source_row, r in enumerate(rows, 1)
               if len(r) > COL["ссылка"] and r[COL["ссылка"]].startswith("http")]
    if args.max_profiles > 0:
        targets = targets[:args.max_profiles]
    print(f"Блогеров в таблице: {len(targets)}\n")

    out = []
    for i, (source_row, row) in enumerate(targets, 1):
        nick, link = row[COL["ник"]].strip(), row[COL["ссылка"]].strip()
        st = profile_stats(link, args.videos, brief, args.workdir)
        price = num(row[COL["цена"]])
        fv = st.get("прогноз_просмотров") or 0
        line = {
            "source_row": source_row,
            "sheet_name": args.sheet_name,
            "ник": nick, "ссылка": link,
            "подписчики": st.get("подписчики"),
            "прогноз_просмотров": round(fv) if fv else "",
            "прогноз_охватов": round(st.get("прогноз_охватов") or 0) if fv else "",
            "ER": f"{st.get('er', 0):.1f}%" if fv else "",
            "CPV": f"{price / fv:.2f} ₽" if (price and fv) else "",
            "комментарий": verdict(st, row),
            "детали": {
                "роликов": st.get("роликов"),
                "выбросов": st.get("выбросов"),
                "среднее_без_выбросов": st.get("среднее_без_выбросов"),
                "er": st.get("er"),
                "er_views": st.get("er_views"),
                "среднее_реакций": st.get("среднее_реакций"),
                "без_текста": st.get("без_текста"),
                "ролики": st.get("ролики_детали"),
                "план_просмотров": num(row[COL["прогноз_просмотров"]]),
                "план_cpv": row[COL["cpv"]].strip(),
                "цена": price,
                "разброс": st.get("разброс"),
                "мат": [{"url": v["url"], "хиты": v["мат"]} for v in st.get("мат_ролики", [])],
                "бренды": [{"url": v["url"], "хиты": v["конкуренты"]} for v in st.get("конкур_ролики", [])],
                "ошибка": st.get("ошибка"),
            },
        }
        out.append(line)
        print(f"[{i}/{len(targets)}] {nick:22} подп {str(line['подписчики'] or '—'):>9} | "
              f"просм {str(line['прогноз_просмотров'] or '—'):>8} | ER {line['ER'] or '—':>6} | "
              f"CPV {line['CPV'] or '—':>8} | {line['комментарий'][:70]}")
        (args.workdir / "результат.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    tsv = args.workdir / "для_вставки.tsv"
    with tsv.open("w", encoding="utf-8") as f:
        f.write("Ник\tПодписчики\tПрогноз просмотров\tПрогноз охватов\tER\tCPV\tКомментарий клиента\n")
        for r in out:
            f.write(f"{r['ник']}\t{r['подписчики'] or ''}\t{r['прогноз_просмотров']}\t"
                    f"{r['прогноз_охватов']}\t{r['ER']}\t{r['CPV']}\t{r['комментарий']}\n")
    print(f"\nГотово. Для вставки в таблицу: {tsv}")


if __name__ == "__main__":
    main()
