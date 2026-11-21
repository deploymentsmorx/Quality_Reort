"""Read a photographed inspection table with Claude vision."""

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


def _jpeg_b64(image: Image.Image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return base64.standard_b64encode(buffer.getvalue()).decode("ascii")


def claude_read(image: Image.Image, job_rows: list[dict], purpose: str) -> dict:
    key, model, _source = anthropic_config()
    if not key:
        return {"rows": [], "meta": {}, "warning": "Anthropic API key is not set."}
    from anthropic import Anthropic

    prompt = _prompt(job_rows, purpose)
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


def _prompt(job_rows: list[dict], purpose: str) -> str:
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
      "m2_confident": true
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
"""


def read_document(payload: bytes, filename: str, job_rows: list[dict], purpose: str) -> dict:
    images = load_images(payload, filename)
    warnings: list[str] = []
    extracted: list[dict] = []
    meta = {}
    key, _model, _source = anthropic_config()
    if not key:
        raise ValueError("Anthropic API key is not set. Super admin can add it in Settings.")
    for page_number, image in enumerate(images, start=1):
        try:
            checked = claude_read(image, job_rows, purpose)
        except Exception as exc:
            warnings.append(f"Page {page_number}: Read failed ({exc}).")
            continue
        if checked.get("warning"):
            warnings.append(checked["warning"])
        for field, value in (checked.get("meta") or {}).items():
            if value and not meta.get(field):
                meta[field] = value
        for raw in checked.get("rows") or []:
            if not isinstance(raw, dict):
                continue
            row = _normalize_claude_row(raw)
            row.setdefault("page", page_number)
            extracted.append(row)
    if not extracted:
        raise ValueError("Nothing could be read. Upload the inspection table, not the ballooned drawing, and keep the photo upright.")
    return {
        "rows": extracted,
        "meta": meta,
        "warnings": _unique(warnings),
        "pages": len(images),
        "ocr_preview": "",
    }


def _unique(items: list[str]) -> list[str]:
    seen = []
    for item in items:
        if item and item not in seen:
            seen.append(item)
    return seen


def _normalize_claude_row(claude_row: dict) -> dict:
    notes = []
    output = {
        "balloon_no": _clean_cell(claude_row.get("balloon_no")),
        "page": claude_row.get("page"),
        "work_area": _clean_cell(claude_row.get("work_area")),
        "characteristic": _clean_cell(claude_row.get("characteristic")),
        "nominal": _clean_cell(claude_row.get("nominal")),
        "tol_plus": claude_row.get("tol_plus"),
        "tol_minus": claude_row.get("tol_minus"),
        "unit": _clean_cell(claude_row.get("unit")),
        "instrument": _clean_cell(claude_row.get("instrument")),
        "instrument_id": _clean_cell(claude_row.get("instrument_id")),
        "reader": "claude",
    }
    for field in ("m1", "m2"):
        value = _clean_cell(claude_row.get(field))
        confident = bool(claude_row.get(f"{field}_confident"))
        if _is_hyphen(value):
            output[field] = "-"
            output[f"{field}_confident"] = True
        elif value:
            output[field] = value
            output[f"{field}_confident"] = confident
            if not confident:
                notes.append(f"{field.upper()} needs a look")
        else:
            output[field] = ""
            output[f"{field}_confident"] = False
    output["sheet_result"] = _as_written(claude_row.get("result"))
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
