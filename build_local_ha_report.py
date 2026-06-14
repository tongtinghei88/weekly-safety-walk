from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from PIL import Image

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
    before: Path | None = None
    after: Path | None = None
    method: str = "needs-review"
    reason: str = (
        "No confident match from issue text and photo contents. "
        "The runner does not use photo timestamps for placement; inspect the photos and add photo_mapping.json."
    )


@dataclass(frozen=True)
class PhotoProfile:
    path: Path
    green_cover: float
    yellow_barrier: float
    generator: float
    blue_left: float
    block_storage: float


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

    return sorted(photos, key=lambda path: path.name.lower())


def validate_photo_count(photos: list[Path], issue_count: int) -> None:
    expected = issue_count * 2
    if len(photos) != expected:
        raise RuntimeError(
            f"Photo count mismatch: found {len(photos)} photo(s), "
            f"but {issue_count} issue(s) require {expected} photo(s)."
        )


def image_ratio(pixels: list[tuple[int, int, int]], predicate: Any) -> float:
    if not pixels:
        return 0.0
    return sum(1 for red, green, blue in pixels if predicate(red, green, blue)) / len(pixels)


def image_box_ratio(image: Image.Image, x0: float, x1: float, y0: float, y1: float, predicate: Any) -> float:
    width, height = image.size
    left = max(0, min(width, int(width * x0)))
    right = max(left + 1, min(width, int(width * x1)))
    top = max(0, min(height, int(height * y0)))
    bottom = max(top + 1, min(height, int(height * y1)))
    total = 0
    matched = 0
    for y in range(top, bottom):
        for x in range(left, right):
            total += 1
            if predicate(*image.getpixel((x, y))):
                matched += 1
    return matched / total if total else 0.0


def build_photo_profile(path: Path) -> PhotoProfile:
    image = Image.open(path).convert("RGB")
    image.thumbnail((320, 240))
    data = image.get_flattened_data() if hasattr(image, "get_flattened_data") else image.getdata()
    pixels = list(data)

    green = image_ratio(
        pixels,
        lambda red, green, blue: green > 70 and green > red * 1.18 and green > blue * 1.08 and green - red > 20,
    )
    yellow = image_ratio(
        pixels,
        lambda red, green, blue: red > 130 and green > 105 and blue < 110 and abs(red - green) < 90,
    )
    red_area = image_ratio(
        pixels,
        lambda red, green, blue: red > 115 and red > green * 1.35 and red > blue * 1.35,
    )
    black = image_ratio(pixels, lambda red, green, blue: red < 75 and green < 75 and blue < 75)
    brown = image_ratio(
        pixels,
        lambda red, green, blue: red > 80 and green > 45 and blue < 90 and red > green * 1.15,
    )
    blue_left = image_box_ratio(
        image,
        0.0,
        0.25,
        0.0,
        1.0,
        lambda red, green, blue: blue > 105 and blue > red * 1.15 and blue > green * 0.85,
    )

    return PhotoProfile(
        path=path,
        green_cover=(green * 100) - (yellow * 20) - (red_area * 10),
        yellow_barrier=(yellow * 100) + (brown * 20) - (red_area * 20),
        generator=(red_area * 120) + (black * 30) - (yellow * 80) - (green * 80),
        blue_left=blue_left * 100,
        block_storage=(black * 30) + (brown * 30) - (yellow * 80) - (red_area * 40) - (green * 100),
    )


def issue_pairing_kind(issue: str, action: str) -> str | None:
    text = " ".join(f"{issue} {action}".lower().split())
    if "paving block" in text and ("cover" in text or "plastic sheet" in text):
        return "covered-blocks"
    if "fire extinguisher" in text and "generator" in text:
        return "generator-extinguisher"
    return None


def top_unused(
    profiles: list[PhotoProfile],
    used: set[Path],
    score_name: str,
    count: int,
    minimum_score: float | None = None,
) -> list[PhotoProfile]:
    candidates = [profile for profile in profiles if profile.path not in used]
    candidates.sort(key=lambda profile: (-getattr(profile, score_name), profile.path.name.lower()))
    selected = candidates[:count]
    if len(selected) < count:
        return []
    if minimum_score is not None and any(getattr(profile, score_name) < minimum_score for profile in selected):
        return []
    return selected


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


def auto_pairs(job_dir: Path, photos: list[Path], issues: list[str], actions: list[str]) -> list[PhotoPair]:
    issue_count = len(issues)
    validate_photo_count(photos, issue_count)

    profiles = [build_photo_profile(photo) for photo in photos]
    resolved: list[PhotoPair | None] = [None] * issue_count
    used: set[Path] = set()

    for index, (issue, action) in enumerate(zip(issues, actions)):
        kind = issue_pairing_kind(issue, action)
        pair: PhotoPair | None = None
        if kind == "covered-blocks":
            after_candidates = top_unused(profiles, used, "green_cover", 1, minimum_score=8.0)
            if after_candidates:
                after = after_candidates[0]
                before_candidates = top_unused(
                    [profile for profile in profiles if profile.path != after.path],
                    used | {after.path},
                    "block_storage",
                    1,
                )
                if before_candidates:
                    before = before_candidates[0]
                    pair = PhotoPair(
                        before=before.path,
                        after=after.path,
                        method="description-aware",
                        reason="Matched paving-block issue: BEFORE is the strongest block/storage photo; AFTER is the strongest green cover/plastic-sheet photo.",
                    )
        elif kind == "generator-extinguisher":
            candidates = top_unused(profiles, used, "generator", 2, minimum_score=8.0)
            if len(candidates) == 2:
                before, after = sorted(candidates, key=lambda profile: profile.blue_left)
                if after.blue_left - before.blue_left >= 1.0:
                    pair = PhotoPair(
                        before=before.path,
                        after=after.path,
                        method="description-aware",
                        reason="Matched generator/fire-extinguisher issue: both photos score as generator; AFTER has stronger left-side blue extinguisher signal.",
                    )

        if pair:
            resolved[index] = pair
            if pair.before is not None:
                used.add(pair.before)
            if pair.after is not None:
                used.add(pair.after)

    for index, pair in enumerate(resolved):
        if pair is None:
            resolved[index] = PhotoPair()

    return [cast(PhotoPair, pair) for pair in resolved]


def load_override_pairs(job_dir: Path, auto_plan: list[PhotoPair]) -> list[PhotoPair]:
    override_path = job_dir / OVERRIDE_FILENAME
    if not override_path.exists():
        return auto_plan

    data = read_json(override_path)
    resolved = [PhotoPair(before=item.before, after=item.after, method=item.method, reason=item.reason) for item in auto_plan]

    def apply_index(index: int, before: Any | None = None, after: Any | None = None) -> None:
        if index < 1 or index > len(resolved):
            raise IndexError(f"Issue index out of range: {index}")
        pair = resolved[index - 1]
        if before is not None:
            pair.before = resolve_photo_reference(job_dir, before)
            pair.method = "manual-override"
            pair.reason = "Loaded from photo_mapping.json."
        if after is not None:
            pair.after = resolve_photo_reference(job_dir, after)
            pair.method = "manual-override"
            pair.reason = "Loaded from photo_mapping.json."

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
        for photo in (pair.before, pair.after):
            if photo is None:
                continue
            if photo in seen:
                raise RuntimeError("Each photo can only be used once across BEFORE and AFTER.")
            seen.add(photo)

    return resolved


def require_resolved_pairs(pairs: list[PhotoPair], override_path: Path, suggested_path: Path) -> None:
    unresolved = [
        str(index)
        for index, pair in enumerate(pairs, start=1)
        if pair.before is None or pair.after is None
    ]
    if unresolved:
        raise RuntimeError(
            "Photo mapping requires visual review for issue(s): "
            + ", ".join(unresolved)
            + f". Review issue text and photo contents in {suggested_path}, then create or update {override_path}."
        )


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
                "before": pairs[index].before.name if pairs[index].before else None,
                "after": pairs[index].after.name if pairs[index].after else None,
                "method": pairs[index].method,
                "reason": pairs[index].reason,
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

    auto_plan = auto_pairs(job_dir, photos, issues, actions)
    suggested_path = job_dir / SUGGESTED_FILENAME
    write_json(suggested_path, build_suggestion_payload(date, photos, issues, actions, auto_plan))

    plan = load_override_pairs(job_dir, auto_plan)
    require_resolved_pairs(plan, job_dir / OVERRIDE_FILENAME, suggested_path)
    before_photos = [cast(Path, pair.before) for pair in plan]
    after_photos = [cast(Path, pair.after) for pair in plan]

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
