"""Tolerance, gage-range, and OCR-confidence rules for one balloon row.

Measurements outside a gage range are flagged and kept. The instrument
fields stay writable; range never clears or rejects the typed value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


PASS_WORDS = {"OK", "PASS", "P", "GOOD", "ACCEPT", "ACCEPTED", "YES"}
FAIL_WORDS = {"NG", "NOK", "FAIL", "F", "REJECT", "REJECTED", "BAD", "NO"}


@dataclass
class Verdict:
    result: str
    range_warning: bool
    id_mismatch: bool
    reasons: list[str] = field(default_factory=list)
    lsl: float | None = None
    usl: float | None = None
    m1_status: str = "empty"
    m2_status: str = "empty"


def blank(value) -> bool:
    return value is None or str(value).strip() == ""


NA_WORDS = {"-", "—", "–", "NA", "N/A", "NIL", "/"}


def parse_number(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(" ", "").replace(",", ".")
    text = re.sub(r"[°º˚]", "", text)
    if not text:
        return None
    text = text.replace("±", "")
    try:
        return float(text)
    except ValueError:
        return None


def parse_tol(value) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    number = parse_number(str(value).replace("+", "").replace("−", "-").replace("–", "-"))
    if number is None:
        return None
    return abs(number)


def split_nominal(nominal, tol_plus, tol_minus):
    """Pull ± or +/+ forms out of a single nominal cell when tolerances are empty."""
    plus = parse_tol(tol_plus)
    minus = parse_tol(tol_minus)
    text = "" if nominal is None else str(nominal).strip()
    if plus is None and minus is None and text:
        bilateral = re.match(
            r"^([+-]?\d+(?:[.,]\d+)?)\s*(?:±|\+/-|\+/?-)\s*(\d+(?:[.,]\d+)?)$",
            text,
        )
        if bilateral:
            return (
                bilateral.group(1).replace(",", "."),
                float(bilateral.group(2).replace(",", ".")),
                float(bilateral.group(2).replace(",", ".")),
            )
        unequal = re.match(
            r"^([+-]?\d+(?:[.,]\d+)?)\s*\+\s*(\d+(?:[.,]\d+)?)\s*/\s*-\s*(\d+(?:[.,]\d+)?)$",
            text,
        )
        if unequal:
            return (
                unequal.group(1).replace(",", "."),
                float(unequal.group(2).replace(",", ".")),
                float(unequal.group(3).replace(",", ".")),
            )
    return text, plus, minus


def balloon_key(value) -> str:
    text = "" if value is None else str(value).strip().upper()
    text = re.sub(r"^(BALLOON|BAL|ITEM|NO\.?|#)\s*", "", text)
    text = text.replace(" ", "")
    if re.fullmatch(r"\d+", text):
        return str(int(text))
    return text


def norm_measure(value) -> str | None:
    if blank(value):
        return None
    text = str(value).strip()
    number = parse_number(text)
    if number is not None and re.search(r"\d", text):
        rendered = f"{number:.4f}".rstrip("0").rstrip(".")
        return rendered
    return re.sub(r"\s+", "", text).upper()


def _limits(nominal: str, tol_plus, tol_minus):
    nominal_text, plus, minus = split_nominal(nominal, tol_plus, tol_minus)
    nominal_number = parse_number(nominal_text)
    if nominal_number is None:
        return nominal_text, None, None, plus, minus
    if plus is None and minus is None:
        return nominal_text, None, None, None, None
    plus = 0.0 if plus is None else plus
    minus = 0.0 if minus is None else minus
    return nominal_text, nominal_number - minus, nominal_number + plus, plus, minus


def _near(left: float, right: float) -> bool:
    return abs(left - right) <= 0.0001


def _on_limit(number: float, nominal: float, plus: float, minus: float) -> bool:
    if plus > 0 and _near(number, nominal + plus):
        return True
    if minus > 0 and _near(number, nominal - minus):
        return True
    return False


def _is_na(raw) -> bool:
    if raw is None:
        return False
    return str(raw).strip().upper() in NA_WORDS


def _one_measurement(raw, nominal, plus, minus) -> tuple[str, str | None]:
    """Green inside Dim ± Tol, orange exactly on a limit, red outside, missed if blank."""
    if _is_na(raw):
        return "na", None
    specified = nominal is not None and (plus is not None or minus is not None)
    plus = 0.0 if plus is None else plus
    minus = 0.0 if minus is None else minus
    if blank(raw):
        return ("missed", "missed") if specified else ("empty", None)
    token = re.sub(r"\s+", "", str(raw).strip()).upper()
    if token in PASS_WORDS:
        return "pass", None
    if token in FAIL_WORDS:
        return "fail", f"{raw} is a reject entry"
    number = parse_number(raw)
    if number is None:
        return "review", "Could not read this as a dimension"
    if not specified:
        return "review", "No Dim ± Tol on this row"
    lsl = nominal - minus
    usl = nominal + plus
    if number < lsl - 1e-9 or number > usl + 1e-9:
        if number > usl:
            return "fail", f"{_fmt(number)} is above {_fmt(usl)}"
        return "fail", f"{_fmt(number)} is below {_fmt(lsl)}"
    if _on_limit(number, nominal, plus, minus):
        return "boundary", f"{_fmt(number)} is on Dim ± Tol"
    return "pass", None


def _fmt(number: float) -> str:
    return f"{number:.4f}".rstrip("0").rstrip(".")


def _catalog_match(instruments: list[dict], name: str, asset: str):
    named = [
        item
        for item in instruments
        if str(item.get("name") or "").strip().casefold() == name.strip().casefold() and name.strip()
    ]
    by_asset = None
    if asset.strip():
        for item in instruments:
            if str(item.get("asset_id") or "").strip().casefold() == asset.strip().casefold():
                by_asset = item
                break
    return named, by_asset


def _outside_range(instrument: dict | None, values: list) -> bool:
    if not instrument:
        return False
    low = instrument.get("range_min")
    high = instrument.get("range_max")
    if low is None or high is None:
        return False
    numbers = [parse_number(value) for value in values]
    numbers = [number for number in numbers if number is not None]
    return any(number < float(low) - 1e-9 or number > float(high) + 1e-9 for number in numbers)


def evaluate(data: dict, instruments: list[dict] | None = None) -> Verdict:
    instruments = instruments or []
    nominal_text, lsl, usl, plus, minus = _limits(
        data.get("nominal") or "",
        data.get("tol_plus"),
        data.get("tol_minus"),
    )
    nominal_number = parse_number(nominal_text)
    m1_status, m1_reason = _one_measurement(data.get("m1"), nominal_number, plus, minus)
    m2_status, m2_reason = _one_measurement(data.get("m2"), nominal_number, plus, minus)
    reasons: list[str] = []
    if m1_reason:
        reasons.append(f"T/W: {m1_reason}")
    if m2_reason:
        reasons.append(f"F/W: {m2_reason}")

    statuses = [m1_status, m2_status]
    if any(status == "fail" for status in statuses):
        result = "fail"
    elif any(status == "boundary" for status in statuses):
        result = "boundary"
    elif any(status == "review" for status in statuses):
        result = "review"
    elif any(status == "pass" for status in statuses) and any(status == "missed" for status in statuses):
        result = "incomplete"
    elif any(status == "pass" for status in statuses):
        result = "pass"
    elif any(status == "missed" for status in statuses):
        result = "missed"
    elif statuses and all(status == "na" for status in statuses):
        result = "na"
    else:
        result = "empty"

    name = str(data.get("instrument") or "")
    asset = str(data.get("instrument_id") or "")
    named, by_asset = _catalog_match(instruments, name, asset)
    gage = named[0] if named else by_asset
    compared = [nominal_text, data.get("m1"), data.get("m2")]
    range_warning = _outside_range(gage, compared)

    id_mismatch = False
    if named and by_asset and named[0].get("name", "").casefold() != str(by_asset.get("name") or "").casefold():
        id_mismatch = True
        reasons.append(f"Instr. ID {asset} is filed under {by_asset.get('name')}")
        if result == "pass":
            result = "review"

    if range_warning and gage is not None:
        low = _fmt(float(gage["range_min"]))
        high = _fmt(float(gage["range_max"]))
        unit = gage.get("unit") or ""
        unit_text = f" {unit}" if unit else ""
        reasons.append(f"Outside {low}–{high}{unit_text} instrument range — value kept")

    if data.get("ocr_note") and result in {"review", "incomplete", "empty"}:
        note = str(data["ocr_note"]).strip()
        if note and note not in reasons:
            reasons.append(note)

    if result == "pass":
        reasons.insert(0, "Inside Dim ± Tol")
    elif result == "boundary" and not any("on Dim" in reason for reason in reasons):
        reasons.insert(0, "On Dim ± Tol")
    elif result == "incomplete":
        missing = [label for label, status in (("T/W", m1_status), ("F/W", m2_status)) if status == "missed"]
        if missing:
            reasons.append(f"{' and '.join(missing)} missed")
    elif result == "missed" and not reasons:
        reasons.append("Actual dimension missed")
    elif result == "empty" and not reasons:
        reasons.append("Empty")

    return Verdict(
        result=result,
        range_warning=range_warning,
        id_mismatch=id_mismatch,
        reasons=reasons,
        lsl=lsl,
        usl=usl,
        m1_status=m1_status,
        m2_status=m2_status,
    )
