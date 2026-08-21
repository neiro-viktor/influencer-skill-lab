import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCREEN_SCRIPT = ROOT / "skills" / "proverka-blogerov" / "scripts" / "demo_screening.py"
SHORTLIST_SCRIPT = ROOT / "skills" / "shortlist-blogerov" / "scripts" / "build_shortlist.py"
VERIFY_SCRIPT = ROOT / "skills" / "shortlist-blogerov" / "scripts" / "verify_handoff.py"
LIVE_SCRIPT = ROOT / "skills" / "proverka-blogerov" / "scripts" / "live_metrics.py"
OPEN_TEMPLATE_SCRIPT = ROOT / "skills" / "proverka-blogerov" / "scripts" / "open_onegroup_template.py"
DEEP_BOOTSTRAP_SCRIPT = ROOT / "skills" / "proverka-blogerov" / "scripts" / "bootstrap_deep.py"
DEEP_CHECK_SCRIPT = ROOT / "skills" / "proverka-blogerov" / "scripts" / "check_roller.py"
MAKE_PASTE_SCRIPT = ROOT / "skills" / "proverka-blogerov" / "scripts" / "make_paste.py"
BRIEF = ROOT / "skills" / "shortlist-blogerov" / "assets" / "campaign-brief.json"

live_spec = importlib.util.spec_from_file_location("live_metrics", LIVE_SCRIPT)
live_metrics = importlib.util.module_from_spec(live_spec)
live_spec.loader.exec_module(live_metrics)

deep_spec = importlib.util.spec_from_file_location("check_roller", DEEP_CHECK_SCRIPT)
check_roller = importlib.util.module_from_spec(deep_spec)
deep_spec.loader.exec_module(check_roller)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        subprocess.run([sys.executable, str(SCREEN_SCRIPT), "--output", str(self.root / "screening")], check=True, capture_output=True, text=True, encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def rows(self, path):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return {row["candidate_id"]: row for row in csv.DictReader(handle)}

    def test_six_cases_cover_expected_branches(self):
        rows = self.rows(self.root / "screening" / "screening.csv")
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows["b01"]["decision"], "Подходит")
        self.assertEqual(rows["b02"]["decision"], "С оговоркой")
        self.assertEqual(rows["b03"]["decision"], "Нужна ручная проверка")
        self.assertEqual(rows["b04"]["decision"], "Не рекомендовать без согласования")
        self.assertEqual(rows["b05"]["outliers_removed"], "1")
        self.assertEqual(rows["b06"]["decision"], "Нужна ручная проверка")

    def test_forecast_formula_spot_check(self):
        rows = self.rows(self.root / "screening" / "screening.csv")
        for candidate_id in ("b01", "b02", "b03", "b04", "b05"):
            expected = round(float(rows[candidate_id]["average_views_clean"]) * 0.9)
            self.assertEqual(int(rows[candidate_id]["forecast_views"]), expected)

    def test_outputs_are_complete(self):
        for name in ("screening.csv", "screening.json", "report.md"):
            self.assertGreater((self.root / "screening" / name).stat().st_size, 100)
        payload = json.loads((self.root / "screening" / "screening.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["source_mode"], "local-demo")

    def test_unavailable_public_sheet_falls_back(self):
        fallback = self.root / "fallback"
        subprocess.run([
            sys.executable, str(SCREEN_SCRIPT), "--sheet-url", "http://127.0.0.1:9/not-there",
            "--output", str(fallback),
        ], check=True, capture_output=True, text=True, encoding="utf-8")
        payload = json.loads((fallback / "screening.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["source_mode"], "local-demo")
        self.assertEqual(len(payload["records"]), 6)

    def test_live_calculation_uses_real_evidence_and_excludes_outlier(self):
        candidate = {
            "participant": "Тест", "handle": "@real", "profile_url": "https://www.tiktok.com/@real",
            "price_rub": "45000", "target_forecast_views": "50000",
        }
        videos = [
            {"url": "https://example.test/1", "views": 10000, "likes": 500, "comments": 20, "shares": 10},
            {"url": "https://example.test/2", "views": 12000, "likes": 600, "comments": 25, "shares": 12},
            {"url": "https://example.test/3", "views": 90000, "likes": 5000, "comments": 200, "shares": 100},
        ]
        row = live_metrics.calculate(candidate, {"followers": 100000}, videos, "2026-08-21T00:00:00+00:00")
        self.assertEqual(row["outliers_removed"], 1)
        self.assertEqual(row["forecast_views"], 9900)
        self.assertEqual(row["decision"], "Брать с оговорками")
        self.assertIn("example.test/1", row["evidence_urls"])
        self.assertNotIn("example.test/3", row["evidence_urls"].split(" | ")[:2])
        self.assertEqual(row["speech_check"], "не проверялась в быстром live-режиме")

    def test_er_over_100_is_not_silently_recommended(self):
        candidate = {"participant": "", "handle": "@viral", "profile_url": "https://www.tiktok.com/@viral", "price_rub": "10000", "target_forecast_views": ""}
        videos = [{"url": "https://example.test/viral", "views": 200000, "likes": 80000, "comments": 1000, "shares": 1000}]
        row = live_metrics.calculate(candidate, {"followers": 50000}, videos, "2026-08-21T00:00:00+00:00")
        self.assertEqual(row["decision"], "Брать с оговорками")
        self.assertIn("выше 100%", row["reason"])

    def test_live_input_template_is_detected_without_network(self):
        input_path = self.root / "profiles.csv"
        input_path.write_text(
            "participant,profile_url,price_rub,target_forecast_views\n"
            "Аня,https://www.tiktok.com/@public,30000,12000\n",
            encoding="utf-8",
        )
        rows = live_metrics.read_candidates(input_path, None, 2)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["participant"], "Аня")
        self.assertEqual(rows[0]["price_rub"], "30000")

    def test_google_sheet_url_becomes_public_csv_export(self):
        url = live_metrics.google_csv_url("https://docs.google.com/spreadsheets/d/demo-id/edit#gid=42")
        self.assertEqual(url, "https://docs.google.com/spreadsheets/d/demo-id/export?format=csv&gid=42")

    def test_template_opener_has_safe_non_browser_mode(self):
        opened = subprocess.run(
            [sys.executable, str(OPEN_TEMPLATE_SCRIPT), "--print-only"],
            check=True, capture_output=True, text=True, encoding="utf-8",
        )
        self.assertIn("1DzqwIBC4nvX2VFtv6q2imZGHwpUxgDmg1jDvoAu90_s/copy", opened.stdout)

    def test_onegroup_headers_and_exact_write_plan(self):
        input_path = self.root / "onegroup.csv"
        input_path.write_text(
            "№,Ник ,Ссылка  на аккаунт ,Подписчики,Стоимость одной публикации,Прогноз просмотров по всем публикациям\n"
            "1,demo_creator,https://www.tiktok.com/@demo_creator,,167300,\n",
            encoding="utf-8",
        )
        candidates = live_metrics.read_candidates(input_path, None, 2)
        self.assertEqual(candidates[0]["price_rub"], "167300")
        self.assertEqual(candidates[0]["source_row"], "2")
        row = live_metrics.calculate(
            candidates[0], {"followers": 100000},
            [{"url": "https://example.test/1", "views": 10000, "likes": 500, "comments": 20, "shares": 10}],
            "2026-08-21T00:00:00+00:00",
        )
        live_metrics.write_outputs([row], [], self.root / "onegroup-out", "https://docs.google.com/spreadsheets/d/copy-id/edit")
        plan = json.loads((self.root / "onegroup-out" / "sheet-write-plan.json").read_text(encoding="utf-8"))
        self.assertTrue(plan["copy_required"])
        self.assertEqual(plan["writes"][0]["followers_range"], "H2")
        self.assertEqual(plan["writes"][0]["analysis_range"], "N2:S2")
        self.assertAlmostEqual(plan["writes"][0]["analysis_values"][2], row["er_pct"] / 100)
        self.assertIn("не проверялись", plan["writes"][0]["analysis_values"][5])

    def test_deep_profanity_and_competitor_detection(self):
        cues = [("00:00:30", "если были бы мозги я бы хуй сюда поехал"),
                ("00:00:40", "мандариновый сок без сахара")]
        profanity = check_roller.find_profanity(cues)
        self.assertEqual([hit["слово"] for hit in profanity], ["хуй"])
        competitors = check_roller.find_competitors(
            [("00:01:06", "тот же самый яндекс маркет")],
            {"Ozon": ["озон"], "Yandex Market": ["яндекс маркет"]},
            "пост создан вместе с Ozon",
        )
        self.assertEqual({hit["бренд"] for hit in competitors}, {"Ozon", "Yandex Market"})

    def test_deep_plan_covers_every_source_row_and_keeps_evidence(self):
        payload = [
            {
                "source_row": 4,
                "sheet_name": "Несогласованные блогеры",
                "ник": "demo_creator",
                "ссылка": "https://www.tiktok.com/@demo_creator",
                "подписчики": 3000000,
                "прогноз_просмотров": 34896,
                "прогноз_охватов": 30345,
                "ER": "0.1%",
                "CPV": "4.79 ₽",
                "детали": {
                    "роликов": 10, "выбросов": 2, "среднее_реакций": 4112,
                    "er": 0.137, "er_views": 10.98, "без_текста": 2,
                    "план_просмотров": None, "план_cpv": "", "разброс": [6597, 100400],
                    "ошибка": None, "бренды": [],
                    "мат": [{"url": "https://www.tiktok.com/@demo_creator/video/7000000000000000001",
                             "хиты": [{"время": "00:00:30", "слово": "хуй",
                                      "фраза": "если были бы мозги я бы хуй сюда поехал"}]}],
                },
            },
            {
                "source_row": 9, "sheet_name": "Несогласованные блогеры", "ник": "clean",
                "ссылка": "https://www.tiktok.com/@clean", "подписчики": 100000,
                "прогноз_просмотров": 10000, "прогноз_охватов": 8696, "ER": "2.5%", "CPV": "3.00 ₽",
                "детали": {"роликов": 10, "выбросов": 0, "среднее_реакций": 2500,
                            "er": 2.5, "er_views": 25, "без_текста": 0,
                            "план_просмотров": None, "план_cpv": "", "разброс": [8000, 12000],
                            "ошибка": None, "бренды": [], "мат": []},
            },
        ]
        source = self.root / "deep-result.json"
        source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        output = self.root / "deep-plan"
        subprocess.run([sys.executable, str(MAKE_PASTE_SCRIPT), "--input", str(source), "--output", str(output)],
                       check=True, capture_output=True, text=True, encoding="utf-8")
        plan = json.loads((output / "sheet-write-plan.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["profiles_processed"], 2)
        self.assertEqual(plan["writes"][0]["followers_range"], "H4")
        self.assertEqual(plan["writes"][1]["analysis_range"], "N9:S9")
        self.assertAlmostEqual(plan["writes"][0]["analysis_values"][2], 0.001)
        comment = plan["writes"][0]["analysis_values"][5]
        self.assertIn("00:00:30", comment)
        self.assertIn("если были бы мозги", comment)
        self.assertIn("@demo_creator/video/7000000000000000001", comment)
        self.assertIn("без распознанной речи: 2", comment)

    def test_deep_bootstrap_defaults_to_full_table_and_ten_videos(self):
        shown = subprocess.run([sys.executable, str(DEEP_BOOTSTRAP_SCRIPT), "--help"],
                               check=True, capture_output=True, text=True, encoding="utf-8")
        self.assertIn("Вся таблица × 10 роликов", shown.stdout)
        source = DEEP_BOOTSTRAP_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('parser.add_argument("--videos", type=int, default=10)', source)
        self.assertIn('parser.add_argument("--max-profiles", type=int, default=0', source)

    def test_youtube_channel_is_routed_to_video_listing(self):
        self.assertEqual(
            live_metrics.listing_url("https://www.youtube.com/@OpenAI"),
            "https://www.youtube.com/@OpenAI/videos",
        )

    def test_shortlist_pipeline(self):
        subprocess.run([
            sys.executable, str(SHORTLIST_SCRIPT),
            "--screening", str(self.root / "screening" / "screening.csv"),
            "--brief", str(BRIEF), "--output", str(self.root / "shortlist"),
        ], check=True, capture_output=True, text=True, encoding="utf-8")
        rows = self.rows(self.root / "shortlist" / "shortlist.csv")
        self.assertEqual(rows["b01"]["shortlist_decision"], "Шорт-лист")
        self.assertEqual(rows["b05"]["shortlist_decision"], "Шорт-лист")
        self.assertEqual(rows["b02"]["shortlist_decision"], "Резерв")
        self.assertEqual(rows["b03"]["shortlist_decision"], "Ручная проверка")
        self.assertEqual(rows["b04"]["shortlist_decision"], "Отклонить")
        summary = json.loads((self.root / "shortlist" / "summary.json").read_text(encoding="utf-8"))
        self.assertLessEqual(summary["selected_budget_rub"], summary["budget_limit_rub"])
        pilot_path = self.root / "shortlist" / "pilot-register.csv"
        with pilot_path.open(encoding="utf-8-sig", newline="") as handle:
            pilot = next(csv.DictReader(handle))
        self.assertEqual(pilot["selected_candidates"], "2")
        self.assertEqual(pilot["selected_budget_rub"], "78000")
        self.assertEqual(pilot["evidence_file"], "shortlist.csv")
        self.assertIn("baseline", pilot["blocker"])
        classroom = subprocess.run([
            sys.executable, str(VERIFY_SCRIPT), "--pilot", str(pilot_path), "--allow-classroom",
        ], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(classroom.returncode, 0, classroom.stdout + classroom.stderr)
        strict = subprocess.run([
            sys.executable, str(VERIFY_SCRIPT), "--pilot", str(pilot_path),
        ], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(strict.returncode, 2)
        self.assertIn("CLASSROOM_ONLY", strict.stdout)

    def test_budget_overflow_moves_worst_cpv_to_reserve(self):
        brief = json.loads(BRIEF.read_text(encoding="utf-8"))
        brief["total_budget_rub"] = 50000
        brief_path = self.root / "tight-brief.json"
        brief_path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
        output = self.root / "tight-shortlist"
        subprocess.run([
            sys.executable, str(SHORTLIST_SCRIPT),
            "--screening", str(self.root / "screening" / "screening.csv"),
            "--brief", str(brief_path), "--output", str(output),
        ], check=True, capture_output=True, text=True, encoding="utf-8")
        rows = self.rows(output / "shortlist.csv")
        self.assertEqual(rows["b01"]["shortlist_decision"], "Шорт-лист")
        self.assertEqual(rows["b05"]["shortlist_decision"], "Резерв")
        self.assertIn("бюджета", rows["b05"]["shortlist_reason"])

    def test_strict_handoff_passes_with_factual_contract(self):
        brief = json.loads(BRIEF.read_text(encoding="utf-8"))
        brief["artifact_version"] = "v2.3.0"
        brief["pilot"]["owner"] = "руководитель инфлюенс-направления"
        brief["pilot"]["baseline_source"] = "выгрузка задач Битрикс за контрольную неделю"
        brief["pilot"]["deployment_contour"] = "корпоративный Codex + Bitrix24"
        brief["pilot"]["blocker"] = "нет"
        brief_path = self.root / "production-brief.json"
        brief_path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
        output = self.root / "production-shortlist"
        subprocess.run([
            sys.executable, str(SHORTLIST_SCRIPT),
            "--screening", str(self.root / "screening" / "screening.csv"),
            "--brief", str(brief_path), "--output", str(output),
        ], check=True, capture_output=True, text=True, encoding="utf-8")
        verified = subprocess.run([
            sys.executable, str(VERIFY_SCRIPT),
            "--pilot", str(output / "pilot-register.csv"),
        ], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        self.assertIn("PASS", verified.stdout)



if __name__ == "__main__":
    unittest.main()
