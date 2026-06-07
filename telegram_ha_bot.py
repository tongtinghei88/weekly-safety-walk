from __future__ import annotations

import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from gmail_ha_actions import fetch_ha_details, write_actions_file
from ha_walk_excel import WALK_DIR, create_walk_excel
from ha_pdf import create_action_pdf


ROOT = Path(__file__).resolve().parent
INBOX_DIR = ROOT / "telegram_photos"
STATE_PATH = INBOX_DIR / "_state.json"
OUTPUT_DIR = Path(
    os.environ.get("HA_IMPROVEMENT_OUTPUT_DIR", str(WALK_DIR / "\u6539\u5584\u76f8"))
)
ALLOW_SELF_SIGNED_SSL = os.environ.get("HA_BOT_ALLOW_SELF_SIGNED_SSL", "").lower() in {
    "1",
    "true",
    "yes",
}
SSL_CONTEXT = ssl._create_unverified_context() if ALLOW_SELF_SIGNED_SSL else None

DEFAULT_ACTIONS: dict[str, list[str]] = {
    "20260428": [
        "Enough anti-collision rods have been provided at the rear of the excavator.",
        "The concrete pipes for gully works have been placed on ground level and provided with proper wedges at each side of the bottom.",
        "The fencing has been provided between the access and material storage area.",
        "The fire extinguisher has been provided beside the generator.",
    ],
    "20260505": [
        "The fire extinguisher has been provided beside the excavator.",
        "The fencing has been provided between the access and material storage area.",
        "The excavated footpath concrete debris has been properly covered with plastic sheet.",
    ],
    "20260512": [
        "The broken webbing sling for the lifting appliance has been removed from site.",
        "The exit notice has been placed on the fencing for the working access.",
        "The fire extinguisher has been provided beside the equipment for gas welding and flame cutting operations.",
    ],
}


@dataclass
class PhotoRef:
    date: str
    number: int
    action_text: str | None


def load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    else:
        state = {}
    state.setdefault("offset", 0)
    state.setdefault("active_jobs", {})
    state.setdefault("walk_types", {})
    state.setdefault("photo_phases", {})
    state.setdefault("next_photo_numbers", {})
    state.setdefault("pending_confirmations", {})
    return state


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def api_request(token: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None
    if params is not None:
        data = urllib.parse.urlencode(params).encode("utf-8")
    request = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(request, timeout=70, context=SSL_CONTEXT) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API error for {method}: {payload}")
    return payload["result"]


def send_message(token: str, chat_id: int, text: str) -> None:
    api_request(token, "sendMessage", {"chat_id": chat_id, "text": text})


def download_file(token: str, file_id: str, out_path: Path) -> None:
    file_info = api_request(token, "getFile", {"file_id": file_id})
    file_path = file_info["file_path"]
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=70, context=SSL_CONTEXT) as response:
        out_path.write_bytes(response.read())


def parse_photo_ref(text: str, active_date: str | None) -> PhotoRef | None:
    normalized = text.strip()
    action_text = None
    if "-" in normalized:
        left, right = normalized.split("-", 1)
        normalized = left.strip()
        action_text = right.strip() or None

    date_match = re.search(r"\b(20\d{6})\b", normalized)
    photo_match = re.search(r"\b(?:photo|p)\s*(\d{1,2})\b", normalized, re.IGNORECASE)
    if not photo_match:
        return None
    date = date_match.group(1) if date_match else active_date
    if not date:
        return None
    number = int(photo_match.group(1))
    if number < 1:
        return None
    return PhotoRef(date=date, number=number, action_text=action_text)


def actions_for_job(job_dir: Path, date: str) -> list[str] | None:
    action_file = job_dir / "actions.json"
    if action_file.exists():
        data = json.loads(action_file.read_text(encoding="utf-8"))
        actions = data.get("actions")
        if isinstance(actions, list) and actions:
            return [str(action) for action in actions]
    return DEFAULT_ACTIONS.get(date)


def ensure_actions_for_job(job_dir: Path, date: str) -> list[str] | None:
    actions = actions_for_job(job_dir, date)
    if actions:
        return actions
    try:
        return write_actions_file(date, job_dir)
    except (FileNotFoundError, RuntimeError, urllib.error.URLError, TimeoutError) as exc:
        print(f"Could not fetch Gmail action text for {date}: {exc}")
        return None


def received_photo_paths(job_dir: Path, prefix: str = "photo") -> list[Path]:
    numbered: list[tuple[int, Path]] = []
    for path in job_dir.glob(f"{prefix}_*.jpg"):
        match = re.match(rf"^{re.escape(prefix)}_(\d+)\.jpg$", path.name, re.IGNORECASE)
        if match:
            numbered.append((int(match.group(1)), path))
    return [path for _, path in sorted(numbered)]


def before_photo_paths(job_dir: Path) -> list[Path]:
    return received_photo_paths(job_dir, "before_photo")


def after_photo_paths(job_dir: Path) -> list[Path]:
    return received_photo_paths(job_dir, "after_photo")


def clear_job_photos(job_dir: Path) -> None:
    for prefix in ("photo", "before_photo", "after_photo"):
        for old_photo in received_photo_paths(job_dir, prefix):
            old_photo.unlink(missing_ok=True)


def normalize_photo_order(job_dir: Path, prefix: str, order: list[int]) -> None:
    photos = received_photo_paths(job_dir, prefix)
    if sorted(order) != list(range(1, len(photos) + 1)):
        raise ValueError("Invalid photo order.")
    temp_paths: list[Path] = []
    for index, photo in enumerate(photos, start=1):
        temp = job_dir / f"_{prefix}_{index}.tmp"
        photo.replace(temp)
        temp_paths.append(temp)
    for final_number, received_number in enumerate(order, start=1):
        temp_paths[received_number - 1].replace(job_dir / f"{prefix}_{final_number}.jpg")


def detect_walk_type(date: str) -> str:
    match = re.fullmatch(r"20\d{2}\d{2}(\d{2})", date)
    if not match:
        return "weekly"
    day = int(match.group(1))
    week_number = ((day - 1) // 7) + 1
    return "weekly" if week_number % 2 == 1 else "biweekly"


def walk_type_label(walk_type: str) -> str:
    return "Bi-Weekly" if walk_type == "biweekly" else "Weekly"


def image_features(image_path: Path) -> dict[str, float]:
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((240, 240), Image.Resampling.LANCZOS)
        pixels = list(image.getdata())

    total = max(len(pixels), 1)
    red = green = blue = yellow = gray = dark = 0
    for r, g, b in pixels:
        if r > 145 and g < 110 and b < 110:
            red += 1
        if g > 120 and r < 130 and b < 130:
            green += 1
        if b > 130 and r < 130 and g < 170:
            blue += 1
        if r > 155 and g > 125 and b < 90:
            yellow += 1
        if abs(r - g) < 18 and abs(g - b) < 18 and 85 < r < 210:
            gray += 1
        if r < 75 and g < 75 and b < 75:
            dark += 1
    return {
        "red": red / total,
        "green": green / total,
        "blue": blue / total,
        "yellow": yellow / total,
        "gray": gray / total,
        "dark": dark / total,
    }


def best_index(scores: list[float], used: set[int]) -> int:
    available = [(score, index) for index, score in enumerate(scores, start=1) if index not in used]
    return max(available)[1]


def guess_photo_order(date: str, received_photos: list[Path]) -> list[int]:
    features = [image_features(photo) for photo in received_photos]
    used: set[int] = set()
    order: list[int] = []

    if date == "20260512":
        photo3_scores = [f["red"] * 1.8 + f["blue"] * 0.8 + f["dark"] * 0.6 for f in features]
        photo3 = best_index(photo3_scores, used)
        used.add(photo3)

        photo2_scores = [f["yellow"] * 1.4 + f["gray"] * 0.8 + f["green"] * 0.6 for f in features]
        photo2 = best_index(photo2_scores, used)
        used.add(photo2)

        photo1_scores = [-f["red"] - f["blue"] - f["yellow"] + f["gray"] * 0.3 for f in features]
        pick = best_index(photo1_scores, used)
        return [pick, photo2, photo3]

    if date == "20260505":
        photo1_scores = [f["yellow"] * 2.0 - f["blue"] * 0.7 for f in features]
        pick = best_index(photo1_scores, used)
        used.add(pick)
        order.append(pick)

        photo2_scores = [f["gray"] * 1.8 + f["yellow"] * 0.5 - f["blue"] * 1.0 for f in features]
        pick = best_index(photo2_scores, used)
        used.add(pick)
        order.append(pick)

        photo3_scores = [f["blue"] * 2.2 + f["gray"] * 0.4 for f in features]
        pick = best_index(photo3_scores, used)
        order.append(pick)
        return order

    return [1, 2, 3]


def maybe_write_caption_action(job_dir: Path, number: int, action_text: str | None) -> None:
    if not action_text:
        return
    action_file = job_dir / "actions.json"
    if action_file.exists():
        data = json.loads(action_file.read_text(encoding="utf-8"))
        actions = list(data.get("actions", []))
    else:
        actions = []
    while len(actions) < number:
        actions.append("")
    actions[number - 1] = action_text
    action_file.write_text(json.dumps({"actions": actions}, indent=2, ensure_ascii=False), encoding="utf-8")


def try_create_pdf(date: str, order: list[int] | None = None) -> Path | None:
    job_dir = INBOX_DIR / date
    received_photos = after_photo_paths(job_dir) or received_photo_paths(job_dir)
    actions = ensure_actions_for_job(job_dir, date)
    if not actions:
        return None
    expected_count = len(actions)
    if len(received_photos) < expected_count:
        return None
    order = order or list(range(1, expected_count + 1))
    if sorted(order) != list(range(1, expected_count + 1)):
        return None
    photos = [received_photos[index - 1] for index in order]
    if not all(photo.exists() for photo in photos):
        return None
    items = [
        {"title": f"Photo {number}:", "text": actions[number - 1], "image": photos[number - 1]}
        for number in range(1, expected_count + 1)
    ]
    return create_action_pdf(items, OUTPUT_DIR / f"{date}.pdf")


def try_create_excel(date: str, walk_type: str, order: list[int] | None = None) -> Path | None:
    job_dir = INBOX_DIR / date
    received_photos = after_photo_paths(job_dir) or received_photo_paths(job_dir)
    before_photos = before_photo_paths(job_dir)
    actions = ensure_actions_for_job(job_dir, date)
    if not actions:
        return None
    expected_count = len(actions)
    if len(received_photos) < expected_count:
        return None
    order = order or list(range(1, expected_count + 1))
    ordered_photos = [received_photos[index - 1] for index in order]
    ordered_before = before_photos[:expected_count] if len(before_photos) >= expected_count else []
    return create_walk_excel(date, walk_type, ordered_photos, ordered_before)


def issue_lines(date: str, walk_type: str) -> list[str]:
    details = fetch_ha_details(date)
    actions = details["actions"]
    job_dir = INBOX_DIR / date
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "actions.json").write_text(json.dumps({"actions": actions}, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [f"Detected: {walk_type_label(walk_type)}", f"Found {len(details['issues'])} issues.", ""]
    for index, issue in enumerate(details["issues"], start=1):
        lines.append(f"Photo {index}:")
        lines.append(issue)
        lines.append("")
    return lines


def send_job_instructions(token: str, chat_id: int, date: str, walk_type: str) -> int:
    try:
        lines = issue_lines(date, walk_type)
    except Exception as exc:
        send_message(token, chat_id, f"Cannot read Gmail issue text for {date}: {exc}")
        return 0
    lines.append("Send BEFORE photos first, in the above order.")
    send_message(token, chat_id, "\n".join(lines))
    actions = actions_for_job(INBOX_DIR / date, date) or []
    return len(actions)


def confirm_before_photos(token: str, state: dict[str, Any], chat_id: int, date: str) -> None:
    chat_key = str(chat_id)
    actions = ensure_actions_for_job(INBOX_DIR / date, date)
    if not actions:
        send_message(token, chat_id, f"Cannot read Gmail action text for {date}.")
        return
    photos = before_photo_paths(INBOX_DIR / date)
    expected_count = len(actions)
    if len(photos) < expected_count:
        send_message(token, chat_id, f"Saved BEFORE photo. Waiting for {expected_count - len(photos)} more BEFORE photo(s).")
        return
    if len(photos) > expected_count:
        send_message(token, chat_id, f"Received {len(photos)} BEFORE photos, but {date} has {expected_count} issue(s). Send /job {date} to start again.")
        return
    state.setdefault("pending_confirmations", {})[chat_key] = {"date": date, "stage": "before", "order": list(range(1, expected_count + 1))}
    save_state(state)
    lines = [f"Received {expected_count} BEFORE photos for {date}.", "BEFORE order:"]
    for number in range(1, expected_count + 1):
        lines.append(f"Photo {number} = received before photo {number}")
    lines.append("")
    lines.append("Reply YES to confirm BEFORE photos, or reply like 2,1,3 to change the order.")
    send_message(token, chat_id, "\n".join(lines))


def confirm_after_photos(token: str, state: dict[str, Any], chat_id: int, date: str) -> None:
    chat_key = str(chat_id)
    actions = ensure_actions_for_job(INBOX_DIR / date, date)
    if not actions:
        send_message(token, chat_id, f"Cannot read Gmail action text for {date}.")
        return
    photos = after_photo_paths(INBOX_DIR / date)
    expected_count = len(actions)
    if len(photos) < expected_count:
        send_message(token, chat_id, f"Saved AFTER photo. Waiting for {expected_count - len(photos)} more AFTER photo(s).")
        return
    if len(photos) > expected_count:
        send_message(token, chat_id, f"Received {len(photos)} AFTER photos, but {date} has {expected_count} issue(s). Send /job {date} to start again.")
        return
    state.setdefault("pending_confirmations", {})[chat_key] = {"date": date, "stage": "final", "order": list(range(1, expected_count + 1))}
    save_state(state)
    lines = [f"Received {expected_count} AFTER photos for {date}.", "Action taken:"]
    for index, action in enumerate(actions, start=1):
        lines.append(f"Photo {index}:")
        lines.append(action)
        lines.append("")
    lines.append("Reply YES to create PDF + Excel, or reply like 2,1,3 to change the AFTER order.")
    send_message(token, chat_id, "\n".join(lines))


def prompt_confirmation(token: str, state: dict[str, Any], chat_id: int, date: str, force: bool = False) -> None:
    job_dir = INBOX_DIR / date
    actions = ensure_actions_for_job(job_dir, date)
    if not actions:
        send_message(token, chat_id, f"Cannot read Gmail action text for {date} yet. Check Gmail setup, then send /done again.")
        return
    expected_count = len(actions)
    received_photos = received_photo_paths(job_dir)
    if len(received_photos) < expected_count:
        send_message(token, chat_id, f"Saved. Waiting for {expected_count - len(received_photos)} more photo(s) for {date}.")
        return
    if len(received_photos) > expected_count:
        send_message(token, chat_id, f"Received {len(received_photos)} photos, but {date} has {expected_count} action item(s). Send /job {date} to start again, or replace photos with captions like Photo 1.")
        return

    order = guess_photo_order(date, received_photos) if expected_count == 3 else list(range(1, expected_count + 1))
    chat_key = str(chat_id)
    state.setdefault("pending_confirmations", {})[chat_key] = {"date": date, "order": order}
    save_state(state)

    label = "Suggested order" if force else "Suggested order"
    lines = [f"Received {expected_count} photos for {date}. {label}:"]
    for final_number, received_number in enumerate(order, start=1):
        lines.append(f"Photo {final_number} = received photo {received_number}")
        lines.append(actions[final_number - 1])
    lines.append("")
    example = ",".join(str(number) for number in range(1, expected_count + 1))
    lines.append(f"Reply YES to create PDF, or reply like {example} to change the order.")
    send_message(token, chat_id, "\n".join(lines))


def handle_text(token: str, state: dict[str, Any], chat_id: int, text: str) -> None:
    stripped = text.strip()
    chat_key = str(chat_id)
    if stripped.startswith("/start") or stripped.startswith("/help"):
        send_message(
            token,
            chat_id,
            "Send /job 20260505. I will check Gmail, show Weekly/Bi-Weekly and the issue text. "
            "Then send BEFORE photos in order. After confirmation, send AFTER photos in order. "
            "Reply YES at the end to create the improvement PDF and the HA walk Excel.",
        )
        return

    pending = state.get("pending_confirmations", {}).get(chat_key)
    if pending and stripped.upper() == "YES":
        date = str(pending["date"])
        stage = str(pending.get("stage", "final"))
        order = [int(number) for number in pending["order"]]
        job_dir = INBOX_DIR / date
        if stage == "before":
            try:
                normalize_photo_order(job_dir, "before_photo", order)
            except ValueError:
                send_message(token, chat_id, "BEFORE photo order is invalid. Please send the order again, e.g. 2,1,3.")
                return
            state.setdefault("pending_confirmations", {}).pop(chat_key, None)
            state.setdefault("photo_phases", {})[chat_key] = "after"
            state.setdefault("next_photo_numbers", {})[chat_key] = 1
            save_state(state)
            lines = [f"BEFORE photos confirmed for {date}.", "", "Now send AFTER photos in the same issue order:"]
            try:
                lines.extend(issue_lines(date, state.get("walk_types", {}).get(chat_key) or detect_walk_type(date))[3:])
            except Exception:
                pass
            send_message(token, chat_id, "\n".join(lines).strip())
            return

        pdf = try_create_pdf(date, order)
        if pdf:
            walk_type = state.get("walk_types", {}).get(chat_key) or detect_walk_type(date)
            state.setdefault("pending_confirmations", {}).pop(chat_key, None)
            save_state(state)
            send_message(token, chat_id, f"PDF created: {pdf}")
            try:
                excel = try_create_excel(date, walk_type, order)
                if excel:
                    send_message(token, chat_id, f"Excel created: {excel}")
            except Exception as exc:
                send_message(token, chat_id, f"PDF created, but Excel was not created: {exc}")
        else:
            send_message(token, chat_id, f"Cannot create PDF yet for {date}. Please check photos and action text.")
        return

    if pending and re.match(r"^\s*\d+(?:\s*,\s*\d+)+\s*$", stripped):
        date = str(pending["date"])
        stage = str(pending.get("stage", "final"))
        actions = ensure_actions_for_job(INBOX_DIR / date, date)
        if not actions:
            send_message(token, chat_id, f"Cannot read Gmail action text for {date}.")
            return
        order = [int(part) for part in re.split(r"\s*,\s*", stripped)]
        expected = list(range(1, len(actions) + 1))
        if sorted(order) != expected:
            send_message(token, chat_id, f"Order must use {', '.join(str(number) for number in expected)} once only.")
            return
        pending["order"] = order
        save_state(state)
        if stage == "before":
            lines = ["Updated BEFORE order:"]
            for final_number, received_number in enumerate(order, start=1):
                lines.append(f"Photo {final_number} = received before photo {received_number}")
            lines.append("")
            lines.append("Reply YES to confirm BEFORE photos.")
        else:
            lines = ["Updated AFTER order:"]
            for final_number, received_number in enumerate(order, start=1):
                lines.append(f"Photo {final_number} = received after photo {received_number}")
            lines.append("")
            lines.append("Reply YES to create PDF + Excel.")
        send_message(token, chat_id, "\n".join(lines))
        return

    if stripped.lower() == "/done":
        active_date = state.get("active_jobs", {}).get(chat_key)
        if not active_date:
            send_message(token, chat_id, "Send /job 20260505 first.")
            return
        phase = state.get("photo_phases", {}).get(chat_key, "before")
        if phase == "after":
            confirm_after_photos(token, state, chat_id, active_date)
        else:
            confirm_before_photos(token, state, chat_id, active_date)
        return

    type_match = re.match(r"^/type\s+(weekly|biweekly|bi-weekly)\s*$", stripped, re.IGNORECASE)
    if type_match:
        active_date = state.get("active_jobs", {}).get(chat_key)
        if not active_date:
            send_message(token, chat_id, "Send /job 20260505 first.")
            return
        walk_type = "biweekly" if "bi" in type_match.group(1).lower() else "weekly"
        state.setdefault("walk_types", {})[chat_key] = walk_type
        save_state(state)
        send_message(token, chat_id, f"Walk type for {active_date} set to {walk_type_label(walk_type)}.")
        return

    job_match = re.match(r"^/job\s+(20\d{6})\s*$", stripped, re.IGNORECASE)
    if job_match:
        date = job_match.group(1)
        walk_type = detect_walk_type(date)
        job_dir = INBOX_DIR / date
        job_dir.mkdir(parents=True, exist_ok=True)
        clear_job_photos(job_dir)
        state.setdefault("active_jobs", {})[chat_key] = date
        state.setdefault("walk_types", {})[chat_key] = walk_type
        state.setdefault("photo_phases", {})[chat_key] = "before"
        state.setdefault("next_photo_numbers", {})[chat_key] = 1
        state.setdefault("pending_confirmations", {}).pop(chat_key, None)
        save_state(state)
        send_message(token, chat_id, f"Active HA job set to {date}. Checking Gmail...")
        count = send_job_instructions(token, chat_id, date, walk_type)
        if count:
            state.setdefault("next_photo_numbers", {})[chat_key] = 1
            save_state(state)
        return

    action_match = re.match(r"^/action\s+(20\d{6})\s+(\d{1,2})\s+(.+)$", stripped, re.IGNORECASE)
    if action_match:
        date, number, action = action_match.group(1), int(action_match.group(2)), action_match.group(3).strip()
        if number < 1:
            send_message(token, chat_id, "Photo number must be 1 or above.")
            return
        maybe_write_caption_action(INBOX_DIR / date, number, action)
        send_message(token, chat_id, f"Saved action text for {date} Photo {number}.")
        return


def handle_photo(token: str, state: dict[str, Any], message: dict[str, Any]) -> None:
    chat_id = int(message["chat"]["id"])
    chat_key = str(chat_id)
    caption = message.get("caption", "")
    active_date = state.get("active_jobs", {}).get(chat_key)
    ref = parse_photo_ref(caption, active_date)
    if not ref:
        if not active_date:
            send_message(token, chat_id, "Send /job 20260505 first, then send photos.")
            return
        next_photo_numbers = state.setdefault("next_photo_numbers", {})
        number = int(next_photo_numbers.get(chat_key, 1))
        actions = ensure_actions_for_job(INBOX_DIR / active_date, active_date)
        if actions and number > len(actions):
            phase_label = state.get("photo_phases", {}).get(chat_key, "before").upper()
            send_message(token, chat_id, f"Already received {len(actions)} {phase_label} photo(s). Send /job {active_date} to start again, or send /done.")
            return
        ref = PhotoRef(date=active_date, number=number, action_text=None)

    photos = message.get("photo") or []
    if not photos:
        return
    phase = state.get("photo_phases", {}).get(chat_key, "before")
    prefix = "after_photo" if phase == "after" else "before_photo"
    actions = ensure_actions_for_job(INBOX_DIR / ref.date, ref.date)
    if actions and ref.number > len(actions):
        send_message(token, chat_id, f"{ref.date} has {len(actions)} issue(s). Photo {ref.number} is too many. Send /job {ref.date} to start again.")
        return
    file_id = photos[-1]["file_id"]
    job_dir = INBOX_DIR / ref.date
    out_path = job_dir / f"{prefix}_{ref.number}.jpg"
    download_file(token, file_id, out_path)
    maybe_write_caption_action(job_dir, ref.number, ref.action_text)
    state.setdefault("next_photo_numbers", {})[chat_key] = ref.number + 1

    phase_label = "AFTER" if phase == "after" else "BEFORE"
    send_message(token, chat_id, f"Saved {ref.date} {phase_label} Photo {ref.number}.")
    if phase == "after":
        confirm_after_photos(token, state, chat_id, ref.date)
    else:
        confirm_before_photos(token, state, chat_id, ref.date)


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN first.")
    if ALLOW_SELF_SIGNED_SSL:
        print("Using self-signed SSL mode for Telegram connection.")

    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    print("Telegram HA bot is running. Press Ctrl+C to stop.")

    while True:
        try:
            updates = api_request(
                token,
                "getUpdates",
                {"offset": int(state.get("offset", 0)), "timeout": 50, "allowed_updates": json.dumps(["message"])},
            )
            for update in updates:
                state["offset"] = max(int(state.get("offset", 0)), int(update["update_id"]) + 1)
                message = update.get("message") or {}
                if "photo" in message:
                    handle_photo(token, state, message)
                elif "text" in message:
                    handle_text(token, state, int(message["chat"]["id"]), message["text"])
                save_state(state)
        except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
            print(f"Temporary bot error: {exc}")
            time.sleep(5)


if __name__ == "__main__":
    main()
