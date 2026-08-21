#!/usr/bin/env python3
"""
Проверка ролика блогера по ссылке — TikTok, VK Клипы, YouTube.

Закрывает шаги процесса Digital:
  2 — метрики поста (просмотры, лайки, комментарии, репосты, автор, дата)
  3 — мат в речи и упоминания конкурентов (речь + описание + текст на экране)
  4 — соответствие ТЗ: артикул, erid, обязательные и запрещённые фразы
  5 — скачивание ролика и обложки для скрина/скринкаста

Транскрипт берётся из субтитров площадки, а если их нет — распознаётся
локально (mlx-whisper), без отправки ролика наружу.

Использование:
    python3 check_roller.py <ссылка> [--brief brief.json] [--keep-video]

brief.json:
{
  "клиент": "Учебный бренд",
  "конкуренты": ["Бренд А", "Бренд Б"],
  "обязательно": ["артикул 12345", "скидка 20%"],
  "запрещено": ["лечит", "гарантия результата"],
  "нужен_erid": true
}
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
YTDLP = os.environ.get("YTDLP_BIN", "yt-dlp")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")
FASTER_WHISPER_MODEL = os.environ.get("FASTER_WHISPER_MODEL", "small")

# Корни русской обсценной лексики. Проверяются как части слова, но с отсечкой
# ложных совпадений по белому списку ниже — иначе «страховка» ловится на «хуй»
# через «ховка», а «постель» на «пост». Урок уже оплачен на дашборде петель.
MAT_ROOTS = [
    "бляд", "блят", "бля",
    "хуй", "хуе", "хуё", "хуя", "хуи",
    "пизд", "пизж",
    "ебал", "ебан", "ебат", "ебуч", "ебло", "ёбан", "ёбар", "заеб", "въеб", "уеб", "наеб",
    "переёб", "перееб", "разъеб", "отъеб", "подъеб",
    "муда", "мудо", "мудак",
    "сук", "сучар", "сучк",
    "гандон", "гондон",
    "пидор", "пидар", "педик",
    "нахер", "нахуй", "похуй", "нихуя", "охуе", "охуи", "дохуя",
    "залуп", "манда", "шлюх",
]

# Слова, которые содержат «матерный» корень, но матом не являются.
MAT_WHITELIST = {
    "сук", "сукно", "сукна", "сукном",  # ветка дерева и ткань; «сука» из списка убрана
    "страховка", "страховки", "трахея", "трахеи",
    "мудрость", "мудрый", "мудрая", "мудрые",
    "сухари", "сухарь", "мудрено",
}

# Слова, которые начинаются с матерного корня, но матом не являются.
# Каждое сюда попало из живой подборки, а не из головы.
WHITE_PREFIXES = (
    "мандарин", "мандат", "мандраж", "мандол",      # манда-
    "бляха", "бляш",                                 # бля-
    "сукин", "сукно", "сукня",                       # сук-
    "мудр",                                          # мудрость; «мудак» остаётся матом
    "страх", "страхов", "трахе",                     # страх, страховка, трахея
    "употреб", "потреб", "требов", "постел", "центр",
    "сухар", "перебо", "переби", "перебр",
)

WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def fetch(url: str, workdir: Path, keep_video: bool):
    """Метаданные + субтитры + медиа."""
    workdir.mkdir(parents=True, exist_ok=True)
    base = str(workdir / "item")

    meta_proc = run([YTDLP, "--socket-timeout", "30", "--skip-download", "--dump-single-json", url])
    if meta_proc.returncode != 0:
        return None, f"не удалось получить данные о ролике: {meta_proc.stderr.strip().splitlines()[-1:]}"
    meta = json.loads(meta_proc.stdout)

    # Субтитры площадки — если есть, распознавание не нужно
    run([YTDLP, "--socket-timeout", "30", "--skip-download", "--write-subs", "--write-auto-subs",
         "--sub-langs", "rus-RU,ru,en,eng-US", "--convert-subs", "vtt", "-o", base, url])

    audio_target = "-x" if not keep_video else "-k"
    dl = [YTDLP, "--socket-timeout", "60", "-x", "--audio-format", "mp3", "-o", base + ".%(ext)s", url]
    if keep_video:
        dl.insert(3, "-k")
    run(dl)

    # Обложка — для скрина публикации
    run([YTDLP, "--socket-timeout", "30", "--skip-download", "--write-thumbnail", "-o", base, url])
    return meta, None


def vtt_to_text(path: Path):
    """VTT → (сплошной текст, реплики с тайм-кодами)."""
    cues, cur_time, buf = [], None, []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if "-->" in line:
            if cur_time and buf:
                cues.append((cur_time, " ".join(buf)))
            cur_time, buf = line.split("-->")[0].strip()[:8], []
        elif line and not line.isdigit() and line != "WEBVTT" and not line.startswith(("Kind:", "Language:")):
            clean = re.sub(r"<[^>]+>", "", line)
            if clean and (not buf or buf[-1] != clean):
                buf.append(clean)
    if cur_time and buf:
        cues.append((cur_time, " ".join(buf)))
    return " ".join(t for _, t in cues), cues


def transcribe_local(audio: Path):
    """Локальное распознавание, если субтитров нет."""
    try:
        import mlx_whisper
    except ImportError:
        mlx_whisper = None
    if mlx_whisper is not None:
        res = mlx_whisper.transcribe(str(audio), path_or_hf_repo=WHISPER_MODEL, language="ru")
        cues = [(f"{int(s['start'])//60:02d}:{int(s['start'])%60:02d}", s["text"].strip())
                for s in res.get("segments", [])]
        return res["text"].strip(), cues, None

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None, None, "не установлен локальный Whisper (mlx-whisper или faster-whisper)"
    model = WhisperModel(FASTER_WHISPER_MODEL, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(audio), language="ru", vad_filter=True)
    cues = []
    for segment in segments:
        text = segment.text.strip()
        if text:
            cues.append((f"{int(segment.start)//60:02d}:{int(segment.start)%60:02d}", text))
    return " ".join(text for _, text in cues), cues, None


def extract_frames(video: Path, every_sec: int = 2):
    """Кадры для визуальной проверки: логотип конкурента на упаковке, вывеска,
    товар в руках — речью такое не ловится. Кадры отдаются модели, которая
    смотрит картинку (Gemini), или сервису распознавания логотипов."""
    if not video.exists():
        return []
    frames_dir = video.parent / "кадры"
    frames_dir.mkdir(exist_ok=True)
    run(["ffmpeg", "-loglevel", "error", "-i", str(video),
         "-vf", f"fps=1/{every_sec},scale=640:-1", "-y", str(frames_dir / "кадр_%03d.jpg")])
    return sorted(frames_dir.glob("*.jpg"))


# ── Словарь ─────────────────────────────────────────────────────────────
# Живёт в slovar.json рядом со скриптом: команда правит его сама, код не трогает.
# Каждое исключение попало туда из живой подборки. Если файла нет — работают
# встроенные списки выше.
_SLOVAR = HERE.parent / "slovar.json"
if _SLOVAR.exists():
    _d = json.loads(_SLOVAR.read_text(encoding="utf-8"))
    MAT_ROOTS = _d.get("мат_корни", MAT_ROOTS)
    PREFIXES = _d.get("приставки")
    WHITE_PREFIXES = tuple(e["слово"] for e in _d.get("исключения", [])
                           if not e.get("точное_совпадение"))
    MAT_WHITELIST = {e["слово"] for e in _d.get("исключения", [])
                     if e.get("точное_совпадение")}
    BRAND_ALIASES = _d.get("бренды_общие_алиасы", {})
else:
    BRAND_ALIASES = {}

# Мат ищется от начала слова или сразу после приставки. Без этого «упоТРЕБЛЯете»,
# «ценТРАХ» и «сТРАХ» летят в отчёт как мат — проверено на живой подборке 31.07.
PREFIXES = PREFIXES if "PREFIXES" in dir() and PREFIXES else [
    "", "на", "по", "за", "у", "при", "до", "пере", "вы", "из", "раз", "от",
    "под", "об", "о", "недо", "пре", "про"]
MAT_RE = re.compile(
    r"^(?:" + "|".join(PREFIXES) + r")?(?:" + "|".join(MAT_ROOTS) + r")",
    re.IGNORECASE,
)


def find_profanity(cues):
    hits = []
    for stamp, text in cues:
        for word in WORD_RE.findall(text.lower()):
            if word in MAT_WHITELIST or len(word) < 3 or word.startswith(WHITE_PREFIXES):
                continue
            if MAT_RE.match(word):
                hits.append({"время": stamp, "слово": word, "фраза": text.strip()})
    return hits


LAT2CYR = str.maketrans({
    "a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х",
    "i": "и", "j": "дж", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п",
    "q": "к", "r": "р", "s": "с", "t": "т", "u": "у", "v": "в", "w": "в", "x": "кс",
    "y": "й", "z": "з",
})


def brand_variants(brand: str, aliases):
    """Блогеры пишут бренды и латиницей, и кириллицей: Ozon → «озоне», Lamoda → «ламоду».
    Берём написание из брифа, его транслитерацию и все алиасы, которые задал менеджер."""
    forms = {brand.lower().strip()}
    forms.add(brand.lower().strip().translate(LAT2CYR))
    forms.update(a.lower().strip() for a in aliases or [])
    forms.update(a.lower().strip() for a in BRAND_ALIASES.get(brand, []))
    # Обрезаем окончание, чтобы ловить падежи: «ламода» → «ламод»
    return {f[: max(4, len(f) - 1)] for f in forms if len(f) >= 3}


def find_competitors(cues, competitors, description):
    """competitors: список строк либо {"Ozon": ["озон", "озоне"]}."""
    if isinstance(competitors, dict):
        pairs = list(competitors.items())
    else:
        pairs = [(b, []) for b in (competitors or [])]

    def matches(text, stems):
        """Сравниваем по НАЧАЛУ слова, а не подстрокой: иначе «не нрАВИТся»
        засчитывается за Avito. Ложное срабатывание в проверке блогера дороже
        пропуска — ему перестают верить с первого раза."""
        for word in WORD_RE.findall((text or "").lower()):
            for s in stems:
                if word.startswith(s):
                    return word
        return None

    hits = []
    for brand, aliases in pairs:
        stems = brand_variants(brand, aliases)
        for stamp, text in cues:
            word = matches(text, stems)
            if word:
                hits.append({"бренд": brand, "где": f"речь, {stamp}",
                             "совпало": word, "фраза": text.strip()})
        word = matches(description, stems)
        if word:
            hits.append({"бренд": brand, "где": "описание поста",
                         "совпало": word, "фраза": "—"})
    return hits


def check_brief(full_text, description, brief):
    haystack = f"{full_text} {description or ''}".lower()
    report = {"обязательное_не_найдено": [], "запрещённое_найдено": [], "erid": None}
    for phrase in brief.get("обязательно", []):
        if phrase.lower() not in haystack:
            report["обязательное_не_найдено"].append(phrase)
    for phrase in brief.get("запрещено", []):
        if phrase.lower() in haystack:
            report["запрещённое_найдено"].append(phrase)
    if brief.get("нужен_erid"):
        m = re.search(r"erid[:\s]*([A-Za-z0-9]+)", f"{full_text} {description or ''}", re.IGNORECASE)
        report["erid"] = m.group(1) if m else "НЕ НАЙДЕН"
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--brief", type=Path)
    ap.add_argument("--keep-video", action="store_true", help="оставить mp4 для скринкаста")
    ap.add_argument("--workdir", type=Path, default=HERE / "out")
    args = ap.parse_args()

    brief = json.loads(args.brief.read_text(encoding="utf-8")) if args.brief else {}
    work = args.workdir / re.sub(r"\W+", "_", args.url)[-60:]

    meta, err = fetch(args.url, work, args.keep_video)
    if err:
        print(f"❌ {err}")
        sys.exit(1)

    # Русская дорожка всегда в приоритете: на английском переводе площадки
    # русский мат не ловится, а именно он и проверяется.
    def lang_rank(p: Path):
        lang = p.name.split(".")[-2].lower()
        return (0 if lang.startswith("rus") or lang == "ru" else 1, lang)

    subs = sorted(work.glob("item*.vtt"), key=lang_rank)
    source, full_text, cues = None, "", []
    if subs:
        full_text, cues = vtt_to_text(subs[0])
        source = f"субтитры площадки ({subs[0].name.split('.')[-2]})"
    if not subs or not full_text.strip():
        audio = work / "item.mp3"
        if not audio.exists():
            print("❌ нет ни субтитров, ни аудио")
            sys.exit(1)
        full_text, cues, terr = transcribe_local(audio)
        if terr:
            print(f"❌ {terr}")
            sys.exit(1)
        source = "локальное распознавание речи"

    description = meta.get("description") or ""
    mat = find_profanity(cues)
    comp = find_competitors(cues, brief.get("конкуренты", []), description)
    brief_report = check_brief(full_text, description, brief) if brief else None

    out = {
        "ссылка": args.url,
        "площадка": meta.get("extractor_key"),
        "автор": meta.get("uploader") or meta.get("channel"),
        "дата": meta.get("upload_date"),
        "длительность_сек": meta.get("duration"),
        "метрики": {
            "просмотры": meta.get("view_count"),
            "лайки": meta.get("like_count"),
            "комментарии": meta.get("comment_count"),
            "репосты": meta.get("repost_count"),
        },
        "источник_транскрипта": source,
        "мат": mat,
        "конкуренты": comp,
        "соответствие_ТЗ": brief_report,
        "транскрипт": full_text,
    }
    (work / "отчёт.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'='*60}\nРОЛИК: {out['автор']} · {out['площадка']} · {out['дата']}")
    print(f"Просмотры {out['метрики']['просмотры']} · лайки {out['метрики']['лайки']} · "
          f"комментарии {out['метрики']['комментарии']}")
    print(f"Транскрипт: {source}, {len(full_text)} символов")
    print(f"\nМАТ: {'НЕТ' if not mat else f'НАЙДЕН — {len(mat)}'}")
    for h in mat[:10]:
        print(f"   [{h['время']}] «{h['слово']}» — {h['фраза'][:70]}")
    print(f"\nКОНКУРЕНТЫ: {'не упомянуты' if not comp else f'НАЙДЕНЫ — {len(comp)}'}")
    for h in comp[:10]:
        print(f"   {h['бренд']} — {h['где']}: {h['фраза'][:70]}")
    if brief_report:
        print(f"\nТЗ: не найдено обязательного — {brief_report['обязательное_не_найдено'] or 'всё на месте'}")
        print(f"    запрещённое — {brief_report['запрещённое_найдено'] or 'нет'}")
        if brief_report["erid"]:
            print(f"    erid — {brief_report['erid']}")
    print(f"\nФайлы: {work}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
