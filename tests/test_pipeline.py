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
BRIEF = ROOT / "skills" / "shortlist-blogerov" / "assets" / "campaign-brief.json"


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
