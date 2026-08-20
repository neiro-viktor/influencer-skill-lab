#!/usr/bin/env python3
"""Verify that a pilot register can be handed from an author to an operator."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


REQUIRED = {
    "pilot_id", "process_name", "owner", "status", "artifact_version",
    "baseline_value", "baseline_unit", "baseline_source", "target_value",
    "measurement_system", "deployment_contour", "dependencies",
    "blocker", "review_after_days", "evidence_file", "handoff_rule",
}


def configure_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def main() -> int:
    configure_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--allow-classroom", action="store_true")
    args = parser.parse_args()
    with args.pilot.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 1:
        print(f"FAIL: ожидалась одна строка пилота, получено {len(rows)}")
        return 1
    row = rows[0]
    missing_columns = REQUIRED - set(row)
    empty = sorted(key for key in REQUIRED if not str(row.get(key, "")).strip())
    if missing_columns or empty:
        print("FAIL: неполный контракт передачи: " + ", ".join(sorted(missing_columns | set(empty))))
        return 1
    classroom_markers = ("учебн", "заменить фактическим", "synthetic", "локальн")
    contract_text = " ".join(
        row[key] for key in (
            "owner", "baseline_source", "handoff_rule", "deployment_contour",
            "artifact_version", "status",
        )
    ).lower()
    blocker = row["blocker"].strip().lower()
    no_blocker = blocker in {"нет", "отсутствует", "none", "—", "не выявлен"}
    classroom = any(marker in contract_text for marker in classroom_markers) or not no_blocker
    if classroom and not args.allow_classroom:
        print("CLASSROOM_ONLY: структура полная, но baseline, владелец, контур или блокер ещё не готовы")
        return 2
    evidence = args.pilot.parent / row["evidence_file"]
    if not evidence.is_file() or evidence.stat().st_size == 0:
        print(f"FAIL: не найден файл доказательства {evidence}")
        return 1
    print("PASS: контракт передачи пилота полный" if not classroom else "PASS_CLASSROOM: учебный контракт полный; перед рабочим запуском заменить baseline и владельца")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
