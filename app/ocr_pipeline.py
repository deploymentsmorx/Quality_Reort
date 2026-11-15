"""Read a photographed inspection table.

Tesseract is the server OCR. Printed balloon numbers and neat block digits
usually come back. Claude checks those readings against the page. Cursive or
disputed cells are left blank so someone can type them.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
from pathlib import Path

from PIL import Image, ImageOps

from app.checks import balloon_key, blank, norm_measure, parse_tol, split_nominal

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / "data" / "settings.json"
MAX_PAGES = 8
MAX_EDGE = 2200


def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_settings(data: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    current = load_settings()
    current.update(data)
    SETTINGS_PATH.write_text(json.dumps(current), encoding="utf-8")


def anthropic_config() -> tuple[str | None, str, str]:
    env_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    stored = load_settings()
    key = env_key or str(stored.get("anthropic_api_key") or "").strip() or None
    source = "env" if env_key else ("settings" if key else "")
    model = (
        os.environ.get("ANTHROPIC_MODEL", "").strip()
        or str(stored.get("anthropic_model") or "").strip()
        or "claude-sonnet-5-5"
    )
    return key, model, source


def configure_tesseract() -> str | None:
    import pytesseract

    cmd = os.environ.get("TESSERACT_CMD", "").strip()
    candidates = [
        cmd,
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            pytesseract.pytesseract.tesseract_cmd = candidate
            tessdata = Path(candidate).parent / "tessdata"
            if (tessdata / "eng.traineddata").exists():
                os.environ["TESSDATA_PREFIX"] = str(tessdata)
            return candidate
    return None


def tesseract_status() -> dict:
    try:
        import pytesseract
    except ImportError:
        return {"ok": False, "version": None, "error": "pytesseract is not installed"}
    configure_tesseract()
    try:
        version = str(pytesseract.get_tesseract_version())
        return {"ok": True, "version": version, "cmd": pytesseract.pytesseract.tesseract_cmd}
    except Exception as exc:  # pragma: no cover - depends on the machine
        return {"ok": False, "version": None, "error": str(exc)}


def load_images(payload: bytes, filename: str) -> list[Image.Image]:
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".pdf" or payload[:4] == b"%PDF":
        import pymupdf

        document = pymupdf.open(stream=payload, filetype="pdf")
        images = []
        for index, page in enumerate(document):
            if index >= MAX_PAGES:
                break
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            images.append(_prepare(image))
        document.close()
        if not images:
            raise ValueError("That PDF has no pages.")
        return images
    image = Image.open(io.BytesIO(payload))
    image = ImageOps.exif_transpose(image)
    if getattr(image, "n_frames", 1) > 1:
        frames = []
        for index in range(min(image.n_frames, MAX_PAGES)):
            image.seek(index)
            frames.append(_prepare(image.convert("RGB")))
        return frames
    return [_prepare(image.convert("RGB"))]


def _prepare(image: Image.Image) -> Image.Image:
    image = image.convert("RGB")
    long_edge = max(image.size)
    if long_edge > MAX_EDGE:
        scale = MAX_EDGE / long_edge
        image = image.resize((int(image.width * scale), int(image.height * scale)), Image.Resampling.LANCZOS)
    return image


def _ocr_image(image: Image.Image):
    import pytesseract

    configure_tesseract()
    gray = ImageOps.autocontrast(ImageOps.grayscale(image))
    if gray.width < 1400:
        scale = 1400 / gray.width
        gray = gray.resize((int(gray.width * scale), int(gray.height * scale)), Image.Resampling.LANCZOS)
    config = "--psm 6"
    text = pytesseract.image_to_string(gray, config=config)
    data = pytesseract.image_to_data(gray, output_type=pytesseract.Output.DICT, config=config)
    return text, data, gray.width


def tesseract_table(image: Image.Image) -> dict:
    status = tesseract_status()
    if not status["ok"]:
        return {"rows": [], "text": "", "warning": status.get("error") or "Tesseract is not available"}
    try:
        text, data, width = _ocr_image(image)
    except Exception as exc:
        return {"rows": [], "text": "", "warning": f"Tesseract failed: {exc}"}
    rows = _rows_from_tsv(data, width)
    return {"rows": rows, "text": text, "warning": None}


def _rows_from_tsv(data: dict, width: int) -> list[dict]:
    grouped: dict[tuple, list[dict]] = {}
    count = len(data.get("text") or [])
    for index in range(count):
        text = (data["text"][index] or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][index])
        except (TypeError, ValueError):
            conf = -1
        if conf < 0:
            continue
        key = (data["block_num"][index], data["par_num"][index], data["line_num"][index])
        grouped.setdefault(key, []).append(
            {
                "text": text,
                "conf": conf,
                "left": int(data["left"][index]),
                "top": int(data["top"][index]),
                "right": int(data["left"][index]) + int(data["width"][index]),
            }
        )
    lines = []
    for words in grouped.values():
        words.sort(key=lambda word: word["left"])
        top = min(word["top"] for word in words)
        lines.append({"top": top, "words": words, "text": " ".join(word["text"] for word in words)})
    lines.sort(key=lambda line: line["top"])

    header_index = None
    columns = None
    for index, line in enumerate(lines):
        columns = _header_columns(line["words"], width)
        if columns and "balloon_no" in columns and ({"m1", "nominal", "characteristic"} & set(columns)):
            header_index = index
            break
    if header_index is None or not columns:
        return []

    rows = []
    order = sorted(columns, key=lambda name: columns[name][0])
    for line in lines[header_index + 1 :]:
        if _looks_like_header(line["text"]):
            continue
        cells = {name: [] for name in columns}
        for word in line["words"]:
            center = (word["left"] + word["right"]) / 2
            owner = _column_at(columns, center)
            if owner:
                cells[owner].append(word)
        balloon = " ".join(word["text"] for word in cells.get("balloon_no", [])).strip()
        if not balloon or not re.search(r"[A-Za-z0-9]", balloon):
            continue
        if balloon_key(balloon) in {"BALLOON", "CHARACTERISTIC", "NOMINAL"}:
            continue
        row = {
            "balloon_no": balloon,
            "characteristic": _join(cells, "characteristic"),
            "nominal": _join(cells, "nominal"),
            "tol_plus": _join(cells, "tol_plus"),
            "tol_minus": _join(cells, "tol_minus"),
            "tol": _join(cells, "tol"),
            "unit": _join(cells, "unit"),
            "instrument": _join(cells, "instrument"),
            "instrument_id": _join(cells, "instrument_id"),
            "m1": _join(cells, "m1"),
            "m2": _join(cells, "m2"),
            "result": _join(cells, "result"),
            "m1_confident": _cell_confident(cells, "m1"),
            "m2_confident": _cell_confident(cells, "m2"),
        }
        rows.append(row)
    return rows


def _join(cells: dict, name: str) -> str:
    return " ".join(word["text"] for word in cells.get(name, [])).strip()


def _cell_confident(cells: dict, name: str) -> bool:
    words = cells.get(name) or []
    if not words:
        return False
    if min(word["conf"] for word in words) < 70:
        return False
    text = " ".join(word["text"] for word in words)
    return bool(re.search(r"\d", text))


def _looks_like_header(text: str) -> bool:
    lowered = text.lower()
    return "balloon" in lowered and ("m1" in lowered or "nominal" in lowered)


def _header_columns(words: list[dict], width: int) -> dict[str, tuple[float, float]] | None:
    labeled: list[tuple[str, float, float]] = []
    index = 0
    while index < len(words):
        token = words[index]["text"]
        nxt = words[index + 1]["text"] if index + 1 < len(words) else ""
        pair = f"{token} {nxt}".lower().replace(".", "")
        single = token.lower().replace(".", "")
        name = None
        span = 1
        if single == "tol" and nxt.startswith("+"):
            name = "tol_plus"
            span = 2
        elif single == "tol" and nxt[:1] in {"-", "−"}:
            name = "tol_minus"
            span = 2
        elif pair in {"sl no", "si no", "s no"} or single in {"sl", "si"} and nxt.lower().startswith("no"):
            name = "balloon_no"
            span = 2
        elif pair in {"instr id", "instrument id", "gage id", "gauge id"} or single in {"instr"} and nxt.lower().startswith("id"):
            name = "instrument_id"
            span = 2
        elif single in {"m1", "m2", "unit", "nominal", "balloon"}:
            name = {"balloon": "balloon_no"}.get(single, single)
        elif single in {"tol+", "+tol"} or single.replace(" ", "") in {"tol+"}:
            name = "tol_plus"
        elif single in {"tol-", "-tol"}:
            name = "tol_minus"
        elif single in {"tol", "tolerance"}:
            name = "tol"
        elif single in {"t/w", "tw", "(t/w)"}:
            name = "m1"
        elif single in {"f/w", "fw", "(f/w)"}:
            name = "m2"
        elif single in {"result", "remarks", "remark"}:
            name = "result"
        elif single in {"instrument", "instr", "gage", "gauge"}:
            name = "instrument"
        elif single in {"dim", "dimension"}:
            name = "nominal"
        elif single in {"characteristic", "description", "requirement", "parameter"}:
            name = "characteristic"
        elif pair.startswith("tol +") or token.lower() in {"tol+"}:
            name = "tol_plus"
        if name:
            end_word = words[index + span - 1]
            labeled.append((name, words[index]["left"], end_word["right"]))
            index += span
        else:
            index += 1
    if not labeled:
        return None
    labeled.sort(key=lambda item: item[1])
    # Drop duplicate names, keeping the first occurrence.
    seen = set()
    unique = []
    for item in labeled:
        if item[0] in seen:
            continue
        seen.add(item[0])
        unique.append(item)
    columns = {}
    for index, (name, left, right) in enumerate(unique):
        prev_right = unique[index - 1][2] if index else 0
        next_left = unique[index + 1][1] if index + 1 < len(unique) else width
        start = 0 if index == 0 else (prev_right + left) / 2
        end = width if index + 1 == len(unique) else (right + next_left) / 2
        columns[name] = (start, end)
    return columns


def _column_at(columns: dict[str, tuple[float, float]], center: float) -> str | None:
    for name, (start, end) in columns.items():
        if start <= center <= end:
            return name
    return None


def _jpeg_b64(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return base64.standard_b64encode(buffer.getvalue()).decode("ascii")


def claude_read(image: Image.Image, draft: dict, job_rows: list[dict], purpose: str) -> dict:
    key, model, _source = anthropic_config()
    if not key:
        return {"rows": [], "meta": {}, "warning": "Anthropic API key is not set."}
    from anthropic import Anthropic

    prompt = _prompt(draft, job_rows, purpose)
    client = Anthropic(api_key=key)
    content = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": _jpeg_b64(image)},
        },
        {"type": "text", "text": prompt},
    ]
    message = _create(client, model, content)
    text = _message_text(message)
    parsed = _parse_json(text)
    rows = parsed.get("rows") if isinstance(parsed, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Claude did not return a row list.")
    meta_keys = (
        "part_number",
        "part_name",
        "revision",
        "drawing_number",
        "customer",
        "report_date",
        "serial_number",
        "company_name",
        "material",
        "finish_treatment",
        "supplier_name",
        "authorized_signatory",
        "doc_number",
        "mass",
    )
    return {"rows": rows, "meta": {key: parsed.get(key) for key in meta_keys}, "warning": None}


def _create(client, model: str, content: list):
    kwargs = {"model": model, "max_tokens": 16000, "messages": [{"role": "user", "content": content}]}
    try:
        return client.messages.create(**kwargs, output_config={"effort": "medium"})
    except TypeError:
        return client.messages.create(**kwargs)
    except Exception as exc:
        message = str(exc).lower()
        if "output_config" in message or "effort" in message:
            return client.messages.create(**kwargs)
        raise


def _message_text(message) -> str:
    parts = []
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(parts)


def _parse_json(text: str) -> dict:
    fenced = re.search(r"```(?:json)?\s*(.*)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("No JSON object in the model response.")
    return json.loads(text[start : end + 1])


def _prompt(draft: dict, job_rows: list[dict], purpose: str) -> str:
    known = [
        {
            "balloon_no": row.get("balloon_no"),
            "page": row.get("page_no"),
            "characteristic": row.get("characteristic"),
            "nominal": row.get("nominal"),
        }
        for row in job_rows
    ]
    return f"""You read one page of a CTQ inspection report. Computer-printed text is the standard. Blue or black pen is what must be copied.

Printed columns: Sl no, Parameter Description, Design Spec Dim, Design Spec Tol, Instrument Used.
Pen columns: Actual Dimension (T/W), Actual Dimension (F/W), and Result.
Put T/W in m1, F/W in m2, and the Result column in result. Copy each pen cell exactly as written, such as OK or -.

Purpose: {purpose}
- report: read the printed standard and the pen measurements
- filled: read only the pen measurements and match them to Sl no
- plan: read the printed standard and leave m1 and m2 null

Tesseract draft (printed Sl no is often right; pen is often wrong):
{json.dumps(draft.get("rows") or [], ensure_ascii=False)}

Tesseract text:
{(draft.get("text") or "")[:5000]}

Known rows already on the job. Match these Sl numbers when the print agrees:
{json.dumps(known, ensure_ascii=False)}

Return only JSON:
{{
  "part_number": null,
  "part_name": null,
  "revision": null,
  "drawing_number": null,
  "customer": null,
  "report_date": null,
  "serial_number": null,
  "company_name": null,
  "material": null,
  "finish_treatment": null,
  "supplier_name": null,
  "authorized_signatory": null,
  "doc_number": null,
  "mass": null,
  "rows": [
    {{
      "balloon_no": "52",
      "characteristic": "Dimension",
      "nominal": "189",
      "tol_plus": 2,
      "tol_minus": 2,
      "instrument": "MT",
      "m1": "190",
      "m2": "189",
      "result": "OK",
      "m1_confident": true,
      "m2_confident": true,
      "disputes_tesseract": false
    }}
  ]
}}

Header, copied from the top of the sheet:
- company_name is the company in the top band.
- doc_number is the Doc No, such as F-8.6-02/R0.
- customer, report_date, and drawing_number are the printed or pen values in that header.
- serial_number is the header Sl no written by pen, such as DE-33. It is not a row number in the table.
- part_name is the Part name, such as TMX 150 T3 Chassis.

Rules:
- Copy the pen exactly as written. Do not change it. OK stays OK, not ok. A written number stays that number. Do not correct it toward Dim.
- Copy a hyphen exactly as "-". Do not turn "-" into null or blank. This applies to T/W, F/W, Tol, and Result.
- Tol "±2" means tol_plus 2 and tol_minus 2. Tol "-" means both tolerances null, and the cell still displays "-".
- Degrees such as 30° stay numeric 30, with the degree sign removed.
- Fill every pen cell that can be read, including OK and a hyphen. Leave a cell blank only when the pen cannot be read at all.
- If you disagree with Tesseract, set disputes_tesseract true and keep your value only when the pen is clear.
"""


def read_document(payload: bytes, filename: str, job_rows: list[dict], purpose: str) -> dict:
    images = load_images(payload, filename)
    warnings = []
    extracted: list[dict] = []
    meta = {}
    previews = []
    key, _model, _source = anthropic_config()
    for page_number, image in enumerate(images, start=1):
        draft = tesseract_table(image)
        if draft.get("warning"):
            warnings.append(f"Page {page_number}: {draft['warning']}")
        previews.append((draft.get("text") or "")[:1500])
        page_rows = []
        if key:
            try:
                checked = claude_read(image, draft, job_rows, purpose)
            except Exception as exc:
                warnings.append(f"Page {page_number}: Claude check failed ({exc}). Tesseract readings are unconfirmed.")
                checked = {"rows": [], "meta": {}}
            if checked.get("warning"):
                warnings.append(checked["warning"])
            for field, value in (checked.get("meta") or {}).items():
                if value and not meta.get(field):
                    meta[field] = value
            page_rows = _prefer_claude(draft.get("rows") or [], checked.get("rows") or [])
        else:
            if page_number == 1:
                warnings.append(
                    "Anthropic API key is not set. Tesseract filled clear block digits and left anything unclear blank."
                )
            page_rows = [_from_tesseract_only(row) for row in draft.get("rows") or []]
        for row in page_rows:
            row.setdefault("page", page_number)
            extracted.append(row)
    if not extracted and all(not preview.strip() for preview in previews):
        raise ValueError("Nothing could be read. Upload the inspection table, not the ballooned drawing, and keep the photo upright.")
    return {
        "rows": extracted,
        "meta": meta,
        "warnings": _unique(warnings),
        "pages": len(images),
        "ocr_preview": "\n\n".join(preview for preview in previews if preview.strip())[:4000],
    }


def _unique(items: list[str]) -> list[str]:
    seen = []
    for item in items:
        if item and item not in seen:
            seen.append(item)
    return seen


def _from_tesseract_only(row: dict) -> dict:
    """Without Claude, keep a clear block digit but do not call it confirmed."""
    cleaned = dict(row)
    notes = []
    for field in ("m1", "m2"):
        label = "T/W" if field == "m1" else "F/W"
        if _is_hyphen(cleaned.get(field)):
            cleaned[field] = "-"
            cleaned[f"{field}_confident"] = True
            continue
        if not cleaned.get(f"{field}_confident"):
            if cleaned.get(field):
                notes.append(f"{label} not read")
            cleaned[field] = ""
        elif cleaned.get(field):
            notes.append(f"{label} read by Tesseract — confirm the digit")
        cleaned[f"{field}_confident"] = False
    cleaned["sheet_result"] = _as_written(cleaned.get("result"))
    cleaned["reader"] = "tesseract"
    if notes:
        cleaned["ocr_note"] = "; ".join(notes)
    return cleaned


def _prefer_claude(tesseract_rows: list[dict], claude_rows: list[dict]) -> list[dict]:
    by_balloon = {}
    for row in tesseract_rows:
        by_balloon.setdefault(balloon_key(row.get("balloon_no")), row)
    merged = []
    seen = set()
    for raw in claude_rows:
        if not isinstance(raw, dict):
            continue
        balloon = raw.get("balloon_no")
        if blank(balloon):
            continue
        key = balloon_key(balloon)
        seen.add(key)
        tess = by_balloon.get(key) or {}
        merged.append(_merge_reading(raw, tess))
    for row in tesseract_rows:
        key = balloon_key(row.get("balloon_no"))
        if key in seen:
            continue
        # Claude omitted it. Keep the balloon shell without unconfirmed handwriting.
        kept = _from_tesseract_only(row)
        kept["ocr_note"] = "Balloon seen by Tesseract only"
        merged.append(kept)
    return merged


def _merge_reading(claude_row: dict, tess_row: dict) -> dict:
    dispute = bool(claude_row.get("disputes_tesseract"))
    notes = []
    output = {
        "balloon_no": str(claude_row.get("balloon_no") or tess_row.get("balloon_no") or "").strip(),
        "page": claude_row.get("page") or tess_row.get("page"),
        "work_area": _clean_cell(claude_row.get("work_area")),
        "characteristic": _clean_cell(claude_row.get("characteristic")) or _clean_cell(tess_row.get("characteristic")),
        "nominal": _clean_cell(claude_row.get("nominal")) or _clean_cell(tess_row.get("nominal")),
        "tol_plus": claude_row.get("tol_plus") if claude_row.get("tol_plus") not in (None, "") else tess_row.get("tol_plus"),
        "tol_minus": claude_row.get("tol_minus") if claude_row.get("tol_minus") not in (None, "") else tess_row.get("tol_minus"),
        "unit": _clean_cell(claude_row.get("unit")) or _clean_cell(tess_row.get("unit")),
        "instrument": _clean_cell(claude_row.get("instrument")),
        "instrument_id": _clean_cell(claude_row.get("instrument_id")),
        "reader": "claude",
    }
    if not output["instrument"]:
        output["instrument"] = _clean_cell(tess_row.get("instrument")) if not dispute else ""
    if not output["instrument_id"]:
        output["instrument_id"] = _clean_cell(tess_row.get("instrument_id")) if not dispute else ""
    for field in ("m1", "m2"):
        claude_value = _clean_cell(claude_row.get(field))
        tess_value = _clean_cell(tess_row.get(field))
        confident = bool(claude_row.get(f"{field}_confident"))
        if _is_hyphen(claude_value) or (not claude_value and _is_hyphen(tess_value)):
            output[field] = "-"
            output[f"{field}_confident"] = True
            continue
        if claude_value and tess_value and norm_measure(claude_value) != norm_measure(tess_value):
            if confident:
                output[field] = claude_value
                output[f"{field}_confident"] = False
                notes.append(f"{field.upper()} readers differ (sheet check {claude_value}, Tesseract {tess_value})")
            else:
                output[field] = ""
                output[f"{field}_confident"] = False
                notes.append(f"{field.upper()} not filled — Tesseract guessed {tess_value}")
        elif claude_value:
            output[field] = claude_value
            output[f"{field}_confident"] = confident and not dispute
            if dispute or not confident:
                notes.append(f"{field.upper()} needs a look")
        elif _is_hyphen(tess_value):
            output[field] = "-"
            output[f"{field}_confident"] = True
        else:
            # Claude left it blank on purpose: cursive stays blank.
            output[field] = ""
            output[f"{field}_confident"] = False
            if tess_value:
                notes.append(f"{field.upper()} not read (Tesseract saw {tess_value})")
    output["sheet_result"] = _as_written(claude_row.get("result")) or _as_written(tess_row.get("result"))
    if notes:
        output["ocr_note"] = "; ".join(notes)
    return output


def _is_hyphen(value) -> bool:
    return str(value or "").strip() in {"-", "—", "–"}


def _as_written(value) -> str:
    text = _clean_cell(value)
    if _is_hyphen(text):
        return "-"
    return text


def _clean_cell(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def apply_extraction(
    existing: list[dict],
    extracted: list[dict],
    purpose: str,
    replace: bool,
) -> tuple[list[dict], dict]:
    """Return the full row list and a shop-floor summary. Does not touch the database."""
    rows = [dict(row) for row in existing]
    index_by_key: dict[tuple, list[int]] = {}
    for index, row in enumerate(rows):
        index_by_key.setdefault((balloon_key(row.get("balloon_no")), int(row.get("page_no") or 0)), []).append(index)
        index_by_key.setdefault((balloon_key(row.get("balloon_no")), 0), []).append(index)

    summary = {
        "matched": 0,
        "created_rows": 0,
        "filled_cells": 0,
        "left_blank": [],
        "conflicts_kept": [],
        "warnings": [],
    }
    used = set()

    for item in extracted:
        balloon = balloon_key(item.get("balloon_no"))
        if not balloon:
            continue
        page = item.get("page") or item.get("page_no") or 0
        try:
            page = int(page or 0)
        except (TypeError, ValueError):
            page = 0
        match_index = _pick_match(index_by_key, balloon, page, used)
        if match_index is None:
            created = _new_row(item, len(rows))
            if purpose == "plan":
                created["m1"] = ""
                created["m2"] = ""
                created["sheet_result"] = ""
                created["m1_source"] = "manual"
                created["m2_source"] = "manual"
            rows.append(created)
            match_index = len(rows) - 1
            index_by_key.setdefault((balloon, int(created["page_no"])), []).append(match_index)
            index_by_key.setdefault((balloon, 0), []).append(match_index)
            summary["created_rows"] += 1
        else:
            summary["matched"] += 1
        used.add(match_index)
        _copy_into(rows[match_index], item, purpose, replace, summary)

    summary["matched"] = len(used) - summary["created_rows"]
    return rows, summary


def _pick_match(index_by_key: dict, balloon: str, page: int, used: set[int]) -> int | None:
    candidates = []
    if page:
        candidates.extend(index_by_key.get((balloon, page), []))
    if not candidates:
        candidates.extend(index_by_key.get((balloon, 0), []))
    for index in candidates:
        if index not in used:
            return index
    return None


def _spread_tol(item: dict) -> tuple:
    plus = item.get("tol_plus")
    minus = item.get("tol_minus")
    raw = _clean_cell(item.get("tol"))
    if raw and plus in (None, "") and minus in (None, ""):
        if raw in {"-", "—", "–"}:
            return None, None
        number = parse_tol(str(raw).replace("±", ""))
        if number is not None:
            return number, number
    return plus, minus


def _new_row(item: dict, sort_index: int) -> dict:
    plus0, minus0 = _spread_tol(item)
    nominal, plus, minus = split_nominal(item.get("nominal"), plus0, minus0)
    page = item.get("page") or item.get("page_no") or 1
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    return {
        "id": None,
        "page_no": page or 1,
        "work_area": _clean_cell(item.get("work_area")),
        "balloon_no": _clean_cell(item.get("balloon_no")),
        "characteristic": _clean_cell(item.get("characteristic")),
        "nominal": nominal,
        "tol_plus": parse_tol(plus) if plus is not None else parse_tol(item.get("tol_plus")),
        "tol_minus": parse_tol(minus) if minus is not None else parse_tol(item.get("tol_minus")),
        "unit": _clean_cell(item.get("unit")) or "mm",
        "instrument": _clean_cell(item.get("instrument")),
        "instrument_id": _clean_cell(item.get("instrument_id")),
        "m1": _as_written(item.get("m1")),
        "m2": _as_written(item.get("m2")),
        "sheet_result": _as_written(item.get("sheet_result") or item.get("result")),
        "m1_confident": bool(item.get("m1_confident")),
        "m2_confident": bool(item.get("m2_confident")),
        "m1_source": "ocr" if _clean_cell(item.get("m1")) else "manual",
        "m2_source": "ocr" if _clean_cell(item.get("m2")) else "manual",
        "ocr_note": _clean_cell(item.get("ocr_note")),
        "sort_index": sort_index,
    }


def _copy_into(target: dict, item: dict, purpose: str, replace: bool, summary: dict) -> None:
    plus0, minus0 = _spread_tol(item)
    if plus0 is not None or minus0 is not None:
        item = {**item, "tol_plus": plus0, "tol_minus": minus0}
    plan_fields = ("characteristic", "nominal", "unit", "work_area")
    if purpose in {"plan", "report"}:
        for field in plan_fields:
            incoming = _clean_cell(item.get(field))
            if incoming and (replace or blank(target.get(field))):
                target[field] = incoming
        nominal, plus, minus = split_nominal(
            target.get("nominal") or item.get("nominal"),
            item.get("tol_plus") if item.get("tol_plus") not in (None, "") else target.get("tol_plus"),
            item.get("tol_minus") if item.get("tol_minus") not in (None, "") else target.get("tol_minus"),
        )
        if nominal and (replace or blank(target.get("nominal")) or target.get("nominal") == item.get("nominal")):
            target["nominal"] = nominal
        for field, value in (("tol_plus", plus if plus is not None else item.get("tol_plus")), ("tol_minus", minus if minus is not None else item.get("tol_minus"))):
            parsed = parse_tol(value)
            if parsed is not None and (replace or target.get(field) is None):
                target[field] = parsed
        page = item.get("page") or item.get("page_no")
        if page and (replace or not target.get("page_no")):
            try:
                target["page_no"] = int(page)
            except (TypeError, ValueError):
                pass

    if purpose == "plan":
        return

    for field in ("instrument", "instrument_id", "m1", "m2", "sheet_result"):
        incoming = _clean_cell(item.get(field))
        current = _clean_cell(target.get(field))
        label = f"{target.get('balloon_no')} {field.upper() if field.startswith('m') else field}"
        if field in {"m1", "m2"} and not incoming:
            if not current:
                summary["left_blank"].append(f"{target.get('balloon_no')} {field.upper()}")
            continue
        if not incoming:
            continue
        if current and not replace and norm_measure(current) != norm_measure(incoming):
            summary["conflicts_kept"].append(f"{label} kept {current} (sheet read {incoming})")
            continue
        if current and not replace and norm_measure(current) == norm_measure(incoming):
            continue
        target[field] = incoming
        summary["filled_cells"] += 1
        if field in {"m1", "m2"}:
            target[f"{field}_source"] = "ocr"
            target[f"{field}_confident"] = bool(item.get(f"{field}_confident"))
    note = _clean_cell(item.get("ocr_note"))
    if note:
        previous = _clean_cell(target.get("ocr_note"))
        if replace or not previous:
            target["ocr_note"] = note
        elif note not in previous:
            target["ocr_note"] = f"{previous}; {note}"
