from app.seal import _seal_crop, draw_test_stamp


def test_upside_down_page_is_turned_so_the_stamp_is_at_the_bottom():
    from app.seal import _stamp_at_bottom

    upright = _stamp_at_bottom(draw_test_stamp().rotate(180, expand=True))
    crop = _seal_crop(upright)
    assert crop is not None
    assert crop.width < 500
    crop = _seal_crop(draw_test_stamp())
    assert crop is not None
    assert crop.width < 500
    assert crop.height < 500
    colors = crop.getcolors(maxcolors=crop.width * crop.height) or []
    assert any(red < 80 and blue > 100 for red, _green, blue in (item[1] for item in colors))
