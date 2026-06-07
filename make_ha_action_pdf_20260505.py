from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


OUT = Path(r"C:\Users\Felix\Documents\HA Weekly Email & Weekly Bi-Weekly\outputs\20260505.pdf")
PHOTO_ROOT = Path(r"C:\Users\Felix\AppData\Local\Temp")

ITEMS = [
    {
        "title": "Photo 1 :",
        "text": "The fire extinguisher has been provided beside the excavator.",
        "image": PHOTO_ROOT / "WhatsApp Image 2026-05-05 at 10.57.16 (3).jpeg",
    },
    {
        "title": "Photo 2 :",
        "text": "The fencing has been provided between the access and material storage area.",
        "image": PHOTO_ROOT / "WhatsApp Image 2026-05-05 at 10.57.16 (1).jpeg",
    },
    {
        "title": "Photo 3:",
        "text": "The excavated footpath concrete debris has been properly covered with plastic sheet.",
        "image": PHOTO_ROOT / "WhatsApp Image 2026-05-05 at 10.57.16 (5).jpeg",
    },
]


def wrap_text(c: canvas.Canvas, text: str, width: float, font_name: str, font_size: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if c.stringWidth(candidate, font_name, font_size) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def draw_photo(c: canvas.Canvas, path: Path, x: float, y_top: float, box_w: float, box_h: float) -> float:
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        tmp = OUT.parent / f"_tmp_{path.stem}.jpg"
        image.save(tmp, quality=90)
        ratio = min(box_w / image.width, box_h / image.height)
        draw_w = image.width * ratio
        draw_h = image.height * ratio
        x0 = x + (box_w - draw_w) / 2
        y0 = y_top - draw_h
        c.drawImage(str(tmp), x0, y0, draw_w, draw_h, preserveAspectRatio=True, mask="auto")
        tmp.unlink(missing_ok=True)
        return y0


def draw_item(c: canvas.Canvas, item: dict[str, object], y: float, available_h: float) -> float:
    page_w, _ = A4
    margin_x = 24 * mm
    text_w = page_w - 2 * margin_x

    c.setFont("Times-Roman", 12)
    c.drawString(margin_x, y, str(item["title"]))
    y -= 7 * mm

    lines = wrap_text(c, str(item["text"]), text_w, "Times-Roman", 12)
    for line in lines:
        c.drawString(margin_x, y, line)
        y -= 5 * mm
    y -= 4 * mm

    photo_h = max(available_h - (16 * mm + len(lines) * 5 * mm), 70 * mm)
    bottom = draw_photo(c, Path(item["image"]), margin_x, y, text_w, photo_h)
    return bottom - 8 * mm


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(OUT), pagesize=A4)
    _, page_h = A4
    margin_y = 22 * mm

    y = page_h - margin_y
    y = draw_item(c, ITEMS[0], y, 100 * mm)
    y -= 4 * mm
    draw_item(c, ITEMS[1], y, 102 * mm)
    c.showPage()

    y = page_h - margin_y
    draw_item(c, ITEMS[2], y, 235 * mm)
    c.save()
    print(OUT)


if __name__ == "__main__":
    main()
