from __future__ import annotations

import copy
import io
import os
import re
import zipfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image
from lxml import etree as ET
from openpyxl.utils.datetime import to_excel


NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"

BASE = Path(
    r"G:\??????\ACEW Safety\Element 6 - Inspection Programme\Site Safety and Environment Walk\Site Safety and Environment Walk No.70(05-03-2026).xlsx"
)
AFTER_DOCX = Path(
    r"G:\??????\ACEW Safety\Element 6 - Inspection Programme\Site Safety and Environment Walk\改善相\20260416.docx"
)
OUT_DIR = Path(r"C:\Users\Felix\Documents\HA Weekly Email & Weekly Bi-Weekly\outputs\site_safety_walk_apr2026")
OUT = OUT_DIR / "Site Safety and Environment Walk No.76(16-04-2026) - completed.xlsx"


def a1_to_rc(a1: str) -> tuple[int, int]:
    m = re.fullmatch(r"([A-Z]+)([0-9]+)", a1)
    if not m:
        raise ValueError(a1)
    col_s, row_s = m.groups()
    col = 0
    for ch in col_s:
        col = col * 26 + ord(ch) - 64
    return int(row_s), col


def ensure_cell(root: ET.Element, ref: str) -> ET.Element:
    sheet_data = root.find(f"{{{NS_MAIN}}}sheetData")
    assert sheet_data is not None
    row_idx, _ = a1_to_rc(ref)
    row = None
    for candidate in sheet_data.findall(f"{{{NS_MAIN}}}row"):
        if int(candidate.attrib["r"]) == row_idx:
            row = candidate
            break
    if row is None:
        row = ET.Element(f"{{{NS_MAIN}}}row", {"r": str(row_idx)})
        sheet_data.append(row)
    for cell in row.findall(f"{{{NS_MAIN}}}c"):
        if cell.attrib.get("r") == ref:
            return cell
    cell = ET.Element(f"{{{NS_MAIN}}}c", {"r": ref})
    row.append(cell)
    row[:] = sorted(row, key=lambda c: a1_to_rc(c.attrib["r"])[1])
    return cell


def clear_cell(cell: ET.Element) -> None:
    style = cell.attrib.get("s")
    cell.attrib.clear()
    if style is not None:
        cell.attrib["s"] = style
    cell.attrib["r"] = cell.attrib.get("r", "")
    for child in list(cell):
        cell.remove(child)


def set_inline(root: ET.Element, ref: str, value: str | None) -> None:
    cell = ensure_cell(root, ref)
    original_ref = cell.attrib.get("r", ref)
    style = cell.attrib.get("s")
    cell.attrib.clear()
    cell.attrib["r"] = original_ref
    if style is not None:
        cell.attrib["s"] = style
    for child in list(cell):
        cell.remove(child)
    if value is None:
        return
    cell.attrib["t"] = "inlineStr"
    is_el = ET.SubElement(cell, f"{{{NS_MAIN}}}is")
    t_el = ET.SubElement(is_el, f"{{{NS_MAIN}}}t")
    if value.startswith(" ") or value.endswith(" ") or "\n" in value:
        t_el.attrib["{http://www.w3.org/XML/1998/namespace}space"] = "preserve"
    t_el.text = value


def set_number(root: ET.Element, ref: str, value: float) -> None:
    cell = ensure_cell(root, ref)
    original_ref = cell.attrib.get("r", ref)
    style = cell.attrib.get("s")
    cell.attrib.clear()
    cell.attrib["r"] = original_ref
    if style is not None:
        cell.attrib["s"] = style
    for child in list(cell):
        cell.remove(child)
    v_el = ET.SubElement(cell, f"{{{NS_MAIN}}}v")
    v_el.text = str(value)


def edit_sheet(xml_bytes: bytes, changes: dict[str, object]) -> bytes:
    xml = xml_bytes.decode("utf-8")
    for ref, value in changes.items():
        xml = replace_cell_xml(xml, ref, value)
    return xml.encode("utf-8")


def cell_col_index(ref: str) -> int:
    _, col = a1_to_rc(ref)
    return col


def build_cell_xml(ref: str, old_attrs: str, value: object) -> str:
    style_match = re.search(r'\bs="([^"]*)"', old_attrs)
    style = f' s="{style_match.group(1)}"' if style_match else ""
    if value is None:
        return f'<c r="{ref}"{style}/>'
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"{style}><v>{value}</v></c>'
    text = escape(str(value))
    space = ' xml:space="preserve"' if str(value).startswith(" ") or str(value).endswith(" ") or "\n" in str(value) else ""
    return f'<c r="{ref}"{style} t="inlineStr"><is><t{space}>{text}</t></is></c>'


def replace_cell_xml(xml: str, ref: str, value: object) -> str:
    pattern = re.compile(rf'<c\b(?=[^>]*\br="{re.escape(ref)}")([^>]*)>(.*?)</c>|<c\b(?=[^>]*\br="{re.escape(ref)}")([^>]*)/>', re.S)

    def repl(match: re.Match[str]) -> str:
        attrs = match.group(1) if match.group(1) is not None else match.group(3)
        return build_cell_xml(ref, attrs or "", value)

    new_xml, count = pattern.subn(repl, xml, count=1)
    if count:
        return new_xml

    row_idx, _ = a1_to_rc(ref)
    row_pattern = re.compile(rf'(<row\b[^>]*\br="{row_idx}"[^>]*>)(.*?)(</row>)', re.S)
    row_match = row_pattern.search(xml)
    if not row_match:
        raise RuntimeError(f"Row {row_idx} not found for {ref}")
    prefix, body, suffix = row_match.groups()
    cells = list(re.finditer(r'<c\b[^>]*\br="([A-Z]+[0-9]+)"[^>]*(?:>.*?</c>|/>)', body, re.S))
    insert_at = len(body)
    target_col = cell_col_index(ref)
    inherited_attrs = ""
    for cell in cells:
        if cell_col_index(cell.group(1)) > target_col:
            insert_at = cell.start()
            break
        inherited_attrs = cell.group(0).split(">", 1)[0]
    new_cell = build_cell_xml(ref, inherited_attrs, value)
    new_body = body[:insert_at] + new_cell + body[insert_at:]
    return xml[: row_match.start()] + prefix + new_body + suffix + xml[row_match.end() :]


def image_bytes_for_ext(img: Image.Image, target: str) -> bytes:
    img = img.copy()
    img.thumbnail((1200, 900), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    ext = Path(target).suffix.lower()
    if ext in {".jpg", ".jpeg"}:
        img.convert("RGB").save(out, format="JPEG", quality=92)
    else:
        img.convert("RGB").save(out, format="PNG")
    return out.getvalue()


def extract_after_images() -> list[Image.Image]:
    images: list[Image.Image] = []
    with zipfile.ZipFile(AFTER_DOCX) as zf:
        for name in sorted(n for n in zf.namelist() if n.startswith("word/media/")):
            data = zf.read(name)
            images.append(Image.open(io.BytesIO(data)).copy())
    if len(images) < 3:
        raise RuntimeError("Expected at least 3 after photos in the DOCX.")
    return images[:3]


def get_drawing2_targets(zf: zipfile.ZipFile) -> dict[str, str]:
    rels = ET.fromstring(zf.read("xl/drawings/_rels/drawing2.xml.rels"))
    targets: dict[str, str] = {}
    for rel in rels.findall(f"{{{NS_PKG_REL}}}Relationship"):
        rid = rel.attrib["Id"]
        target = rel.attrib["Target"].replace("../", "xl/")
        targets[rid] = target
    return targets


def update_workbook_calc(xml_bytes: bytes) -> bytes:
    return xml_bytes


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    inspect_date = datetime(2026, 4, 16)
    due_date = datetime(2026, 4, 22)

    issues = [
        "The socket of blower should be properly termination of wiring to avoid exposure of live conductors.",
        "The D.I manhole cover and frame should be properly covered with plastic sheet.",
        "The worker wear safety helmet without using Y-type chin strap during excavating the trench for ducting works.",
    ]
    actions = [
        "The wiring termination at the socket of the blower has been properly rectified",
        "The D.I. manhole cover and frame have been properly covered with plastic sheets",
        "The worker has been reminded to properly use the Y-type chin strap",
    ]
    after_images = extract_after_images()
    blank = Image.new("RGB", (900, 650), "white")

    replacements: dict[str, bytes] = {}
    with zipfile.ZipFile(BASE, "r") as src:
        targets = get_drawing2_targets(src)
        # In the weekly template drawing2, rId1-3 are the before-photo slots.
        # The right-hand after-photo anchors are rId4 (photo 1), rId6 (photo 2), rId5 (photo 3).
        for rid in ("rId1", "rId2", "rId3"):
            replacements[targets[rid]] = image_bytes_for_ext(blank, targets[rid])
        replacements[targets["rId4"]] = image_bytes_for_ext(after_images[0], targets["rId4"])
        replacements[targets["rId6"]] = image_bytes_for_ext(after_images[1], targets["rId6"])
        replacements[targets["rId5"]] = image_bytes_for_ext(after_images[2], targets["rId5"])

        cover_changes = {
            "B3": "Report No.: 76",
            "M6": to_excel(inspect_date),
            "M7": "09:30 A.M.",
            "E7": "Hing Wah Street West, Kwai On Road",
            "C19": issues[0],
            "C20": issues[1],
            "C21": issues[2],
            "M31": "Patrick, P. T. KO\nCE/T243",
        }
        rect_changes = {
            "D20": "Hing Wah Street West",
            "L20": "Hing Wah Street West",
            "D21": issues[0],
            "L21": actions[0],
            "D41": "Hing Wah Street West",
            "L41": "Hing Wah Street West",
            "D42": issues[1],
            "L42": actions[1],
            "D62": "Kwai On Road",
            "L62": "Kwai On Road",
            "D63": issues[2],
            "L63": actions[2],
        }
        safety_changes = {
            # Clear old photo marks from the weekly template.
            "H19": None,
            "J19": None,
            "F19": "ü",
            "H124": None,
            "J124": None,
            "F124": "ü",
            "H139": None,
            "J139": None,
            "F139": "ü",
            # Current Gmail issues.
            "F66": None,
            "H66": "ü",
            "J66": "Photo No.1",
            "F43": None,
            "H43": "ü",
            "J43": "Photo No.2",
            "F131": None,
            "H131": "ü",
            "J131": "Photo No.3",
        }
        anti_changes = {
            "J29": "Patrick, P. T. KO\nCE/T243",
        }

        with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                data = src.read(item.filename)
                if item.filename == "xl/worksheets/sheet1.xml":
                    data = edit_sheet(data, cover_changes)
                elif item.filename == "xl/worksheets/sheet2.xml":
                    data = edit_sheet(data, rect_changes)
                elif item.filename == "xl/worksheets/sheet3.xml":
                    data = edit_sheet(data, safety_changes)
                elif item.filename == "xl/worksheets/sheet5.xml":
                    data = edit_sheet(data, anti_changes)
                elif item.filename == "xl/workbook.xml":
                    data = update_workbook_calc(data)
                elif item.filename in replacements:
                    data = replacements[item.filename]
                dst.writestr(item, data)

    print(OUT)


if __name__ == "__main__":
    main()
