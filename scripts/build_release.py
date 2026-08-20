#!/usr/bin/env python3
"""Build clean UTF-8 skill archives without macOS metadata."""

from pathlib import Path
import argparse
import unicodedata
import zipfile


IGNORED = {".DS_Store", "__MACOSX"}


def allowed(path: Path) -> bool:
    return not any(part in IGNORED or part.startswith("._") or part == "__pycache__" for part in path.parts)


def archive(skill: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(skill.rglob("*")):
            if path.is_file() and allowed(path.relative_to(skill)):
                relative = unicodedata.normalize("NFC", str(Path(skill.name) / path.relative_to(skill)))
                info = zipfile.ZipInfo(relative)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                handle.writestr(info, path.read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    for name in ("proverka-blogerov", "shortlist-blogerov"):
        archive(root / "skills" / name, args.output / f"{name}.zip")
        print(args.output / f"{name}.zip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
