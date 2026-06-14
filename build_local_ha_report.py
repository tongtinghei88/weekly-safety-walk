from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from gmail_ha_actions import fetch_ha_details
from ha_pdf import create_action_pdf
from ha_walk_excel import create_walk_excel


ROOT = Path(__file__).resolve().parent
DEFAULT_PHOTO_ROOT = ROOT / "Photo"
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "Test"
VALID_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
OVERRIDE_FILENAME = "photo_mapping.json"
SUGGESTED_FILENAME = "photo_mapping.suggested.json"


@dataclass
class PhotoPair:
    before: Path
    after: Path


def detect_walk_type(date: str) -> str:
    if len(date) != 8 or not date.isdigit():
        return "weekly"
    day = int(date[6:8])
    week_number = ((day - 1) // 7) + 1
    return "weekly" if week_number % 2 == 1 else "biweekly"


def collect_photos(job_dir: Path) -> list[Path]:
    if not job_dir.exists():
        raise FileNotFoundError(f"Missing photo folder: {job_dir}")
    photos = [
        path
        for path in job_dir.iterdir()
        if path.is_file() and path.suffix.lower() in VALID_IMAGE_SUFFIXES
    ]
    if not photos:
        raise FileNotFoundError(f"No photos found in: {job_dir}")

    def sort_key(path: Path) -> tuple[int, str]:
        match = re.search(r"(\d{8})_(\d{6})", path.stem)
        if match:
            return (int(match.group(1) + match.group(2)), path.name.lower())
        return (0, path.name.lower())

    return sorted(photos, key=sort_key)


def split_even_photos(photos: list[Path], issue_count: int) -> tuple[list[Path], list[Path]]:
    expected = issue_count * 2
    if len(photos) != expected:
        raise RuntimeError(
            f"Photo count mismatch: found {len(photos)} photo(s), "
            f"but {issue_count} issue(s) require {expected} photo(s)."
        )
    return photos[:issue_count], photos[issue_count:]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def resolve_photo_reference(job_dir: Path, value: Any) -> Path:
    if isinstance(value, dict):
        for key in ("file", "name", "path"):
            if value.get(key):
                value = value[key]
                break
    if not isinstance(value, str):
        raise TypeError("Photo reference must be a string filename.")

    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = job_dir / candidate
    candidate = candidate.resolve()
    resolved_job_dir = job_dir.resolve()
    if not candidate.is_relative_to(resolved_job_dir):
        raise ValueError(f"Photo must stay inside the job folder: {value}")
    if not candidate.exists():
        raise FileNotFoundError(f"Missing photo: {candidate}")
    return candidate


def auto_pairs(job_dir: Path, photos: list[Path], issue_count: int) -> list[PhotoPair]:
    before_photos, after_photos = split_even_photos(photos, issue_count)
    return [PhotoPair(before=before_photos[index], after=after_photos[index]) for index in range(issue_count)]


def load_override_pairs(job_dir: Path, auto_plan: list[PhotoPair]) -> list[PhotoPair]:
    override_path = job_dir / OVERRIDE_FILENAME
    if not override_path.exists():
        return auto_plan

    data = read_json(override_path)
    resolved = [PhotoPair(before=item.before, after=item.after) for item in auto_plan]

    def apply_index(index: int, before: Any | None = None, after: Any | None = None) -> None:
        if index < 1 or index > len(resolved):
            raise IndexError(f"Issue index out of range: {index}")
        pair = resolved[index - 1]
        if before is not None:
            pair.before = resolve_photo_reference(job_dir, before)
        if after is not None:
            pair.after = resolve_photo_reference(job_dir, after)

    if isinstance(data.get("issues"), list):
        for index, item in enumerate(data["issues"], start=1):
            if not isinstance(item, dict):
                continue
            issue_index = int(item.get("issue", index))
            apply_index(issue_index, item.get("before"), item.get("after"))

    if isinstance(data.get("before"), dict):
        for key, value in data["before"].items():
            apply_index(int(key), before=value)

    if isinstance(data.get("after"), dict):
        for key, value in data["after"].items():
            apply_index(int(key), after=value)

    if "pairs" in data and isinstance(data["pairs"], list):
        for index, item in enumerate(data["pairs"], start=1):
            if not isinstance(item, dict):
                continue
            apply_index(index, item.get("before"), item.get("after"))

    seen: set[Path] = set()
    for pair in resolved:
        if pair.before in seen or pair.after in seen:
            raise RuntimeError("Each photo can only be used once across BEFORE and AFTER.")
        seen.add(pair.before)
        seen.add(pair.after)

    return resolved


def build_suggestion_payload(
    date: str,
    photos: list[Path],
    issues: list[str],
    actions: list[str],
    pairs: list[PhotoPair],
) -> dict[str, Any]:
    return {
        "date": date,
        "mode": "auto-suggested",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "job_folder": f"Photo/{date}",
        "photos": [photo.name for photo in photos],
        "issues": [
            {
                "issue": index + 1,
                "issue_text": issues[index],
                "action_text": actions[index],
                "before": pairs[index].before.name,
                "after": pairs[index].after.name,
            }
            for index in range(len(pairs))
        ],
    }


def build_local_report(date: str, photo_root: Path, out_dir: Path) -> tuple[Path, Path]:
    job_dir = photo_root / date
    photos = collect_photos(job_dir)
    details = fetch_ha_details(date)
    issues = list(details["issues"])
    actions = list(details["actions"])

    auto_plan = auto_pairs(job_dir, photos, len(issues))
    suggested_path = job_dir / SUGGESTED_FILENAME
    write_json(suggested_path, build_suggestion_payload(date, photos, issues, actions, auto_plan))

    plan = load_override_pairs(job_dir, auto_plan)
    before_photos = [pair.before for pair in plan]
    after_photos = [pair.after for pair in plan]

    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = create_action_pdf(
        [
            {"title": f"Photo {index}:", "text": actions[index - 1], "image": after_photos[index - 1]}
            for index in range(1, len(actions) + 1)
        ],
        out_dir / f"{date}.pdf",
    )
    excel_path = create_walk_excel(
        date,
        detect_walk_type(date),
        after_photos,
        before_photos,
        out_dir,
    )
    return pdf_path, excel_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Weekly Safety Walk PDF and Excel from local photos.")
    parser.add_argument("date", help="Report date in YYYYMMDD format")
    parser.add_argument(
        "--photo-root",
        type=Path,
        default=DEFAULT_PHOTO_ROOT,
        help=f"Root folder that contains YYYYMMDD photo folders (default: {DEFAULT_PHOTO_ROOT})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output folder for generated files (default: {DEFAULT_OUTPUT_DIR})",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pdf_path, excel_path = build_local_report(args.date, args.photo_root, args.output_dir)
    print(f"PDF: {pdf_path}")
    print(f"Excel: {excel_path}")


if __name__ == "__main__":
    main()
