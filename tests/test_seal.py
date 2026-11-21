from app.seal import _seal_crop, draw_test_stamp


def test_blue_seal_is_cropped_from_the_page():
    crop = _seal_crop(draw_test_stamp())
    assert crop is not None
    assert crop.width < 500
    assert crop.height < 500
    colors = crop.getcolors(maxcolors=crop.width * crop.height) or []
    assert any(red < 80 and blue > 100 for red, _green, blue in (item[1] for item in colors))
