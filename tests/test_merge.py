from app.ocr_pipeline import apply_extraction


def _existing():
    return [
        {
            "id": 1,
            "page_no": 1,
            "work_area": "Mill",
            "balloon_no": "01",
            "characteristic": "Overall length",
            "nominal": "120.00",
            "tol_plus": 0.2,
            "tol_minus": 0.1,
            "unit": "mm",
            "instrument": "",
            "instrument_id": "",
            "m1": "",
            "m2": "120.01",
            "m1_source": "manual",
            "m2_source": "manual",
            "m1_confident": True,
            "m2_confident": True,
            "ocr_note": "",
        }
    ]


def test_filled_sheet_matches_balloon_and_leaves_cursive_blank():
    existing = _existing()
    existing[0]["m2"] = ""
    extracted = [
        {
            "balloon_no": "1",
            "page": 1,
            "instrument": "Digital caliper 0-150",
            "instrument_id": "CAL-014",
            "m1": "120.05",
            "m2": "",
            "m1_confident": True,
            "m2_confident": False,
            "ocr_note": "M2 not read",
        }
    ]
    rows, summary = apply_extraction(existing, extracted, "filled", replace=False)
    assert rows[0]["m1"] == "120.05"
    assert rows[0]["m2"] == ""
    assert rows[0]["instrument"] == "Digital caliper 0-150"
    assert rows[0]["m1_source"] == "ocr"
    assert "01 M2" in summary["left_blank"]
    assert summary["filled_cells"] == 3


def test_existing_typed_value_is_not_overwritten():
    existing = _existing()
    existing[0]["m1"] = "120.00"
    extracted = [{"balloon_no": "1", "m1": "119.50", "m1_confident": True, "m2": "", "instrument": "", "instrument_id": ""}]
    rows, summary = apply_extraction(existing, extracted, "filled", replace=False)
    assert rows[0]["m1"] == "120.00"
    assert summary["conflicts_kept"]


def test_unknown_balloon_is_added():
    extracted = [
        {
            "balloon_no": "9",
            "characteristic": "Width",
            "nominal": "8 ±0.1",
            "m1": "8.02",
            "m1_confident": True,
            "m2": None,
            "instrument": "Caliper",
            "instrument_id": "CAL-9",
        }
    ]
    rows, summary = apply_extraction(_existing(), extracted, "report", replace=False)
    assert summary["created_rows"] == 1
    added = rows[-1]
    assert added["balloon_no"] == "9"
    assert added["nominal"] == "8"
    assert added["tol_plus"] == 0.1
    assert added["tol_minus"] == 0.1
