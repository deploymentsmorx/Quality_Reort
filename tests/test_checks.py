from app.checks import evaluate


GAGES = [
    {"name": "Micrometer 0-25", "asset_id": "MIC-006", "range_min": 0, "range_max": 25, "unit": "mm"},
    {"name": "Digital caliper 0-150", "asset_id": "CAL-014", "range_min": 0, "range_max": 150, "unit": "mm"},
]


def _row(**kwargs):
    base = {
        "nominal": "10.00",
        "tol_plus": 0.05,
        "tol_minus": 0.0,
        "m1": "",
        "m2": "",
        "instrument": "",
        "instrument_id": "",
        "m1_source": "manual",
        "m2_source": "manual",
        "m1_confident": True,
        "m2_confident": True,
        "ocr_note": "",
    }
    base.update(kwargs)
    return base


def test_exact_plus_limit_is_orange_and_zero_side_is_green():
    verdict = evaluate(_row(m1="10.05", m2="10.00"), GAGES)
    assert verdict.result == "boundary"
    assert verdict.m1_status == "boundary"
    assert verdict.m2_status == "pass"
    assert verdict.range_warning is False


def test_above_limit_is_incorrect():
    verdict = evaluate(_row(m1="10.06"), GAGES)
    assert verdict.result == "fail"
    assert any("above" in reason for reason in verdict.reasons)


def test_comma_decimal_and_partial():
    verdict = evaluate(_row(m1="10,02", m2=""), GAGES)
    assert verdict.result == "incomplete"
    assert verdict.m1_status == "pass"
    assert verdict.m2_status == "missed"


def test_outside_instrument_range_keeps_a_correct_value():
    verdict = evaluate(
        _row(
            nominal="120.00",
            tol_plus=0.20,
            tol_minus=0.10,
            m1="120.05",
            m2="120.02",
            instrument="Micrometer 0-25",
            instrument_id="MIC-006",
        ),
        GAGES,
    )
    assert verdict.result == "pass"
    assert verdict.range_warning is True
    assert any("value kept" in reason for reason in verdict.reasons)


def test_unconfirmed_ocr_colour_follows_the_number():
    verdict = evaluate(_row(m1="10.02", m1_source="ocr", m1_confident=False, m2="10.01"), GAGES)
    assert verdict.result == "pass"


def test_attribute_ok_and_reject():
    assert evaluate(_row(nominal="M6x1", tol_plus=None, tol_minus=None, m1="OK", m2="OK"), GAGES).result == "pass"
    assert evaluate(_row(nominal="M6x1", tol_plus=None, tol_minus=None, m1="NG"), GAGES).result == "fail"


def test_symmetric_band_colours_and_a_missed_cell():
    inside = evaluate(_row(nominal="807", tol_plus=3, tol_minus=3, m1="808", m2="807"), GAGES)
    on_limit = evaluate(_row(nominal="807", tol_plus=3, tol_minus=3, m1="810", m2="807"), GAGES)
    outside = evaluate(_row(nominal="807", tol_plus=3, tol_minus=3, m1="811", m2="807"), GAGES)
    missed = evaluate(_row(nominal="807", tol_plus=3, tol_minus=3, m1="", m2=""), GAGES)
    assert inside.result == "pass"
    assert on_limit.result == "boundary"
    assert on_limit.m1_status == "boundary"
    assert outside.result == "fail"
    assert missed.result == "missed"
    assert missed.m1_status == "missed"


def test_dash_is_not_applicable():
    verdict = evaluate(_row(nominal="20", tol_plus=None, tol_minus=None, m1="—", m2="OK"), GAGES)
    assert verdict.m1_status == "na"
    assert verdict.result == "pass"


def test_hyphen_row_stays_a_hyphen():
    verdict = evaluate(_row(nominal="20", tol_plus=2, tol_minus=2, m1="-", m2="-"), GAGES)
    assert verdict.m1_status == "na"
    assert verdict.m2_status == "na"
    assert verdict.result == "na"
